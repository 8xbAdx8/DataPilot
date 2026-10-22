"""Explicit optional Ragas judge on two frozen synthetic development responses."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import time
from importlib.metadata import version
from pathlib import Path

from app.config import Settings
from app.rag_evaluate import load_corpus


def samples(report):
    corpus, hashes = load_corpus()
    if report["corpus"]["sha256"] != hashes["sha256"] or report["split"] != "development":
        raise ValueError("Ragas judge uses reviewed development records only")
    documents = {d["chunk_id"]: d["content"] for d in corpus["documents"]}
    cases = {c["id"]: c for c in corpus["queries"]}
    rows = []
    for record in report["records"]:
        if record["id"] not in {"d-p-02", "d-p-04"}:
            continue
        case = cases[record["id"]]
        downstream = record.get("downstream", {})
        if not downstream.get("sql"):
            raise ValueError("Actual SQL response required; no synthetic replacement answer")
        rows.append(
            {
                "id": case["id"],
                "user_input": case["query"],
                "reference": "\n".join(documents[key] for key in case["relevant_chunk_ids"]),
                "retrieved_contexts": [documents[key] for key in record["retrieved_ids"]],
                "response": downstream["sql"],
            }
        )
    if len(rows) != 2:
        raise ValueError("Both preselected actual responses are required")
    return rows


async def judge(report, output):
    # Prevent optional framework telemetry/automatic prompt export, including inherited env.
    os.environ.update(
        RAGAS_DO_NOT_TRACK="true", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false"
    )
    import httpx
    from langsmith import tracing_context
    from openai import AsyncOpenAI
    from ragas.embeddings.base import BaseRagasEmbedding
    from ragas.llms import llm_factory
    from ragas.metrics.collections import AnswerRelevancy, ContextPrecision, ContextRecall

    settings = Settings()
    if not settings.llm_api_key or settings.llm_provider == "mock":
        raise ValueError("Explicitly selected chat judge must be a real configured model")
    requests = []

    async def observe(response):
        await response.aread()
        body = response.json()
        usage = body.get("usage", {})
        requests.append(
            {
                "status": response.status_code,
                "tokens": {
                    k: v
                    for k, v in usage.items()
                    if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                    and type(v) is int
                    and v >= 0
                },
            }
        )

    async def budget(request):
        if len(requests) >= 24:
            raise ValueError("Judge request budget exhausted")

    result = {
        "framework": "ragas",
        "framework_version": version("ragas"),
        "judge_provider": settings.llm_provider,
        "judge_model": settings.llm_model,
        "judge_version": "provider alias; immutable deployment revision unavailable",
        "embedding_model": settings.embedding_model,
        "records": [],
        "requests": requests,
        "response_scope": "actual SQL text, not invented natural-language answers",
        "source_report_sha256": hashlib.sha256(
            json.dumps(report, sort_keys=True).encode()
        ).hexdigest(),
        "cost": None,
        "authority": "supplemental LLM judge; reviewed ID metrics remain authoritative",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.touch(exist_ok=False)
    async with httpx.AsyncClient(
        timeout=30, event_hooks={"request": [budget], "response": [observe]}
    ) as http:
        async with AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            max_retries=0,
            http_client=http,
        ) as chat:
            async with AsyncOpenAI(
                api_key=settings.embedding_api_key,
                base_url=settings.embedding_base_url,
                max_retries=0,
                http_client=http,
            ) as embedding:
                llm = llm_factory(
                    settings.llm_model,
                    client=chat,
                    temperature=0,
                    max_tokens=2048,
                    extra_body={"thinking": {"type": "disabled"}}
                    if settings.llm_provider == "deepseek"
                    else {},
                )

                class FixedDimensionEmbedding(BaseRagasEmbedding):
                    # The collections factory does not accept dimensions. Keep the
                    # independent provider's 1536-dimensional contract explicit.
                    def embed_text(self, text, **kwargs):
                        return self._run_async_in_current_loop(self.aembed_text(text))

                    async def aembed_text(self, text, **kwargs):
                        from app.embeddings import validate_vectors

                        value = await embedding.embeddings.create(
                            model=settings.embedding_model,
                            input=[text],
                            dimensions=1536,
                            encoding_format="float",
                        )
                        if value.model != settings.embedding_model:
                            raise ValueError("Embedding model mismatch")
                        return validate_vectors([value.data[0].embedding], 1)[0]

                emb = FixedDimensionEmbedding()
                scorers = {
                    "context_precision": ContextPrecision(llm=llm),
                    "context_recall": ContextRecall(llm=llm),
                    "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=emb),
                }
                with tracing_context(enabled=False):
                    for sample in samples(report):
                        row = {"id": sample["id"], "metrics": {}}
                        for name, scorer in scorers.items():
                            started = time.perf_counter()
                            arguments = (
                                {k: sample[k] for k in ("user_input", "response")}
                                if name == "answer_relevancy"
                                else {
                                    k: sample[k]
                                    for k in ("user_input", "reference", "retrieved_contexts")
                                }
                            )
                            try:
                                scored = await asyncio.wait_for(
                                    scorer.ascore(**arguments), timeout=90
                                )
                                value = float(scored.value)
                                if not math.isfinite(value):
                                    raise ValueError("Non-finite judge score")
                                measured = {"status": "success", "value": value}
                            except Exception as error:
                                # Provider exceptions may include credentials or prompts.
                                measured = {
                                    "status": "error",
                                    "value": None,
                                    "error_type": type(error).__name__,
                                }
                            measured["duration_ms"] = round(
                                (time.perf_counter() - started) * 1000, 3
                            )
                            row["metrics"][name] = measured
                        result["records"].append(row)
                        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--judge-from-chat",
        action="store_true",
        help="Explicitly select the configured chat model as fallible judge",
    )
    args = parser.parse_args()
    if not args.judge_from_chat or os.environ.get("RUN_LIVE_RAGAS") != "1":
        parser.error("Set RUN_LIVE_RAGAS=1 and explicitly choose --judge-from-chat")
    result = asyncio.run(judge(json.loads(args.report.read_bytes()), args.output))
    print(json.dumps({"framework": result["framework"], "records": result["records"]}))
    if any(m["status"] != "success" for row in result["records"] for m in row["metrics"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
