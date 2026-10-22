"""Optional local cross-encoder, isolated in a bounded, killable subprocess."""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

from app.errors import AppError

MODEL = "ms-marco-MultiBERT-L-12"


def rerank(question, passages, settings):
    if settings.rag_reranker == "none" or not passages:
        return passages
    if len(passages) > 16:
        raise AppError(503, "reranker_limit", "重排序候选超过限制。")
    # Never download weights while handling a request. Operators prefetch explicitly.
    directory = settings.reranker_cache_dir / MODEL
    if not directory.is_dir() or not (directory / "flashrank-MultiBERT-L12_Q.onnx").is_file():
        raise AppError(503, "reranker_not_ready", "重排序模型未准备好，请先下载模型。")
    environment = {
        key: os.environ[key]
        for key in ("PATH", "SystemRoot", "TEMP", "TMP", "PYTHONPATH")
        if key in os.environ
    }
    environment.update(OMP_NUM_THREADS="2", ORT_NUM_THREADS="2", LANGSMITH_TRACING="false")
    try:
        result = subprocess.run(
            [str(settings.reranker_python or sys.executable), "-m", "app.reranking", "--worker"],
            input=json.dumps(
                {
                    "query": question,
                    "passages": [
                        {"id": p["chunk_id"], "text": p["title"] + "\n" + p["excerpt"]}
                        for p in passages
                    ],
                    "cache": str(settings.reranker_cache_dir),
                }
            ),
            text=True,
            capture_output=True,
            timeout=settings.reranker_timeout_seconds,
            check=True,
            env=environment,
        )
        if len(result.stdout) > 32768:
            raise ValueError("oversized ranking result")
        scores = json.loads(result.stdout)
        allowed = {p["chunk_id"]: p for p in passages}
        if len(scores) != len(allowed) or {r["id"] for r in scores} != set(allowed):
            raise ValueError("Reranker changed candidate identities")
        ranked = []
        for row in scores:
            score = row["score"]
            if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("Invalid reranker score")
            if score >= settings.reranker_min_score:
                ranked.append({**allowed[row["id"]], "reranker_score": float(score)})
        return sorted(ranked, key=lambda p: (-p["reranker_score"], p["chunk_id"]))
    except subprocess.TimeoutExpired as error:
        # Fail closed: no unranked fallback silently bypasses the calibrated gate.
        raise AppError(504, "reranker_timeout", "重排序超时，本次未提供知识。") from error
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError) as error:
        raise AppError(503, "reranker_failed", "重排序失败，本次未提供知识。") from error


def main():
    if sys.argv[1:] == ["--prefetch"]:
        from flashrank import Ranker

        from app.config import Settings

        Ranker(model_name=MODEL, cache_dir=str(Settings().reranker_cache_dir), max_length=256)
        return
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("Use --prefetch explicitly to download, or internal --worker")
    payload = json.loads(sys.stdin.read(32769))
    if len(payload["passages"]) > 16 or not Path(payload["cache"]).is_dir():
        raise SystemExit(2)
    from flashrank import Ranker, RerankRequest

    ranker = Ranker(model_name=MODEL, cache_dir=payload["cache"], max_length=256)
    rows = ranker.rerank(RerankRequest(query=payload["query"], passages=payload["passages"]))
    print(json.dumps([{"id": r["id"], "score": float(r["score"])} for r in rows]))


if __name__ == "__main__":
    main()
