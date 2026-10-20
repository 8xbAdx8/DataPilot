import json
import math
import os

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.embeddings import (
    CompatibleEmbeddingProvider,
    FakeEmbeddingProvider,
    get_embedding_provider,
    validate_vectors,
)
from app.errors import AppError


def vector(index=0):
    return [1.0 if i == index else 0.0 for i in range(1536)]


def config():
    return Settings(
        _env_file=None,
        embedding_provider="openai",
        embedding_api_key="embedding-test-only",
        llm_api_key="chat-key-must-not-be-reused",
    )


def test_fake_is_explicit_deterministic_and_separate_from_real_semantic_space():
    provider = FakeEmbeddingProvider()
    first = provider.embed(["销售额 revenue"])[0]
    assert first == provider.embed(["销售额 revenue"])[0]
    assert first != provider.embed(["库存 inventory"])[0]
    assert len(first) == 1536 and math.isclose(math.hypot(*first), 1)
    assert provider.source == "fake" and provider.space.startswith("fake/")


def test_missing_embedding_never_reuses_chat_key_or_falls_back_to_fake():
    settings = Settings(_env_file=None, llm_api_key="chat-key-must-not-be-reused")
    with pytest.raises(AppError) as error:
        get_embedding_provider(settings)
    assert error.value.detail["code"] == "embedding_not_configured"
    settings.embedding_provider = "openai"
    with pytest.raises(AppError):
        get_embedding_provider(settings).embed(["test"])
    with pytest.raises(ValidationError):
        Settings(_env_file=None, embedding_dimensions=768)


@pytest.mark.parametrize(
    "bad",
    [
        [0.0] * 1536,
        [1.0] * 3,
        [float("nan")] * 1536,
        [float("inf")] * 1536,
        [True] * 1536,
        ["1"] * 1536,
    ],
)
def test_invalid_dimension_values_or_zero_norm_are_rejected(bad):
    with pytest.raises(AppError) as error:
        validate_vectors([bad], 1)
    assert error.value.detail["code"] == "embedding_invalid_response"


def test_real_http_contract_checks_indices_dimensions_model_and_independent_key():
    def handler(request):
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer embedding-test-only"
        assert "chat-key-must-not-be-reused" not in str(request.headers)
        body = json.loads(request.content)
        assert body == {
            "model": "text-embedding-3-small",
            "input": ["a", "b"],
            "dimensions": 1536,
            "encoding_format": "float",
        }
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-3-small",
                "data": [{"index": 1, "embedding": vector(1)}, {"index": 0, "embedding": vector()}],
            },
        )

    settings = config()
    assert "embedding-test-only" not in repr(settings)
    values = CompatibleEmbeddingProvider(settings, httpx.MockTransport(handler)).embed(["a", "b"])
    assert values == [vector(), vector(1)]


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"data": []},
        {"data": [{"index": 0, "embedding": vector()}]},
        {"data": [{"index": True, "embedding": vector()}]},
        {"model": "wrong-model", "data": [{"index": 0, "embedding": vector()}]},
        {"data": [{"index": 0, "embedding": [1, 2]}]},
    ],
)
def test_bad_embedding_envelopes_fail_without_success_values(body):
    provider = CompatibleEmbeddingProvider(
        config(), httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    with pytest.raises(AppError) as error:
        provider.embed(["a"])
    assert error.value.detail["code"] == "embedding_invalid_response"


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_embedding_http_errors_do_not_expose_raw_service_response(status):
    provider = CompatibleEmbeddingProvider(
        config(),
        httpx.MockTransport(lambda _: httpx.Response(status, text="private-upstream-sentinel")),
    )
    with pytest.raises(AppError) as error:
        provider.embed(["a"])
    assert error.value.detail["code"] == "embedding_http_error"
    assert "private-upstream" not in str(error.value.detail)


def test_embedding_timeout_and_semantic_space_identity():
    def timeout(_):
        raise httpx.ReadTimeout("private-upstream-sentinel")

    with pytest.raises(AppError) as error:
        CompatibleEmbeddingProvider(config(), httpx.MockTransport(timeout)).embed(["a"])
    assert error.value.detail["code"] == "embedding_timeout"
    one = CompatibleEmbeddingProvider(config()).space
    other = config()
    other.embedding_base_url = "https://different.invalid/v1"
    assert CompatibleEmbeddingProvider(other).space != one


@pytest.mark.live_llm
def test_real_embedding_requires_its_own_explicit_opt_in():
    if os.environ.get("RUN_LIVE_EMBEDDING") != "1":
        pytest.skip("Real Embedding API requires RUN_LIVE_EMBEDDING=1 and independent credentials")
    settings = Settings()
    assert (
        settings.embedding_provider in {"openai", "openai_compatible"}
        and settings.embedding_api_key
    )
    result = get_embedding_provider(settings).embed(["合成销售指标字典"])
    assert len(result[0]) == 1536 and math.isclose(math.hypot(*result[0]), 1)
