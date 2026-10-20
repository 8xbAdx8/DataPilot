"""Embedding is a separate capability and credential from the SQL/chat model."""

import hashlib
import json
import math
import re
import time
from typing import Protocol

import httpx

from app.config import Settings
from app.errors import AppError
from app.providers import TokenUsage, parse_usage

DIMENSIONS = 1536


class EmbeddingProvider(Protocol):
    model: str
    space: str
    source: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def validate_vectors(vectors, expected_count):
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise AppError(502, "embedding_invalid_response", "Embedding 返回数量不匹配。")
    clean = []
    for vector in vectors:
        if (
            not isinstance(vector, list)
            or len(vector) != DIMENSIONS
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in vector
            )
        ):
            raise AppError(502, "embedding_invalid_response", "Embedding 维度或数值不合法。")
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm <= 1e-12:
            raise AppError(502, "embedding_invalid_response", "Embedding 不得为零向量。")
        clean.append([float(value) / norm for value in vector])
    return clean


class FakeEmbeddingProvider:
    """Deterministic lexical hashes for tests/demo, NEVER a semantic embedding model."""

    model = "fake-lexical-hash-v1"
    space = f"fake/{model}/{DIMENSIONS}"
    source = "fake"

    def embed(self, texts):
        vectors = []
        for text in texts:
            value = text.casefold()
            tokens = re.findall(r"[a-z0-9_]+", value)
            tokens.extend(
                value[i : i + 2]
                for i in range(len(value) - 1)
                if any(ord(c) > 127 for c in value[i : i + 2])
            )
            vector = [0.0] * DIMENSIONS
            for token in tokens or [value]:
                index = (
                    int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % DIMENSIONS
                )
                vector[index] += 1.0
            vectors.append(vector)
        return validate_vectors(vectors, len(texts))


class CompatibleEmbeddingProvider:
    source = "provider"

    def __init__(self, settings: Settings, transport=None):
        self.settings, self.transport = settings, transport
        self.model = settings.embedding_model
        self.usage = TokenUsage()
        # Endpoint participates in identity: equal model names at different services
        # do not establish equal semantic spaces. Never put URL credentials in DB.
        identity = hashlib.sha256(settings.embedding_base_url.rstrip("/").encode()).hexdigest()[:16]
        self.space = f"{settings.embedding_provider}/{self.model}/{DIMENSIONS}/{identity}"
        if len(self.model) > 160 or len(self.space) > 255:
            raise AppError(503, "embedding_not_configured", "Embedding 模型标识过长。")

    def embed(self, texts):
        s = self.settings
        self.usage = TokenUsage()
        deadline = time.monotonic() + s.embedding_timeout_seconds
        if not s.embedding_api_key:
            raise AppError(503, "embedding_not_configured", "尚未独立配置 Embedding API Key。")
        if not texts or len(texts) > 32 or sum(len(text) for text in texts) > 40000:
            raise AppError(422, "embedding_input_limit", "Embedding 输入超过批次限制。")
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=s.embedding_timeout_seconds,
                follow_redirects=False,
            ) as client:
                with client.stream(
                    "POST",
                    f"{s.embedding_base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {s.embedding_api_key}"},
                    json={
                        "model": self.model,
                        "input": texts,
                        "dimensions": DIMENSIONS,
                        "encoding_format": "float",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise AppError(
                            502,
                            "embedding_http_error",
                            "Embedding 服务返回错误，请检查独立配置和余额。",
                        )
                    content = bytearray()
                    for part in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise AppError(
                                504, "embedding_timeout", "Embedding 请求超过总时间限制。"
                            )
                        content.extend(part)
                        if len(content) > 4 * 1024 * 1024:
                            raise AppError(
                                502, "embedding_invalid_response", "Embedding 响应超过大小限制。"
                            )
            body = json.loads(content)
            if not isinstance(body, dict):
                raise ValueError("embedding envelope is not an object")
            if body.get("model") != self.model:
                raise ValueError("embedding model mismatch")
            self.usage = parse_usage(body.get("usage"))
            data = body["data"]
            if (
                any(type(item["index"]) is not int for item in data)
                or len(data) != len(texts)
                or sorted(item["index"] for item in data) != list(range(len(texts)))
            ):
                raise ValueError("embedding indices mismatch")
            return validate_vectors(
                [item["embedding"] for item in sorted(data, key=lambda item: item["index"])],
                len(texts),
            )
        except httpx.TimeoutException as error:
            raise AppError(504, "embedding_timeout", "Embedding 请求超时，未完成索引。") from error
        except httpx.HTTPError as error:
            raise AppError(502, "embedding_unavailable", "无法连接 Embedding 服务。") from error
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            raise AppError(
                502, "embedding_invalid_response", "Embedding 响应格式不合法。"
            ) from error


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider()
    if settings.embedding_provider in {"openai", "openai_compatible"}:
        return CompatibleEmbeddingProvider(settings)
    raise AppError(
        503, "embedding_not_configured", "尚未配置独立的 Embedding Provider，知识索引不可用。"
    )
