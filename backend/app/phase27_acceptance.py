"""Opt-in real prose development trial. No chat, judge, tracing or network fallback."""

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from langsmith import tracing_context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import ROOT, Settings
from app.embeddings import CompatibleEmbeddingProvider
from app.knowledge import retrieve_knowledge
from app.main import app
from app.metric_resolution import MetricRegistry
from app.models import KnowledgeChunk, KnowledgeDocument
from app.phase25_acceptance import LIVE, TOKENIZER, CachedProvider, authorization, seed
from app.prose_evidence import select_prose
from app.rag24_budget import BudgetStop
from app.rag24_evaluate import temporary_database, write_new
from app.rag24_policy import select_knowledge
from app.rag_evaluate import counts
from app.trial_budget import LIMITS, STAGE_LIMITS, InputEstimator, MeteredTransport, TrialLedger

FIXTURE = ROOT / "backend/app/evals/phase27/development-v1.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus():
    receipt = json.loads(FIXTURE.with_suffix(".review.json").read_bytes())
    if receipt != {"status": "human-confirmed", "sha256": digest(FIXTURE)}:
        raise BudgetStop("phase27_labels_not_reviewed")
    return json.loads(FIXTURE.read_bytes())


def engine_for_trial():
    public = Settings(_env_file=ROOT / ".env.example")
    return create_engine(
        make_url(public.database_url).set(host="127.0.0.1", port=15637),
        connect_args={"connect_timeout": 5},
    )


def score(records, configs):
    result = {}
    for config in configs:
        rows = []
        for r in records:
            expected, actual = set(r["relevant_ids"]), r[config["id"]]["ids"][:4]
            hits = [int(a in expected) for a in actual]
            first = next((i for i, hit in enumerate(hits, 1) if hit), None)
            dcg = sum(hit / math.log2(i + 1) for i, hit in enumerate(hits, 1))
            ideal = sum(1 / math.log2(i + 1) for i in range(1, min(4, len(expected)) + 1))
            rows.append(
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "positive": bool(expected),
                    "recall_at_4": len(set(actual) & expected) / len(expected)
                    if expected
                    else None,
                    "precision_at_4": sum(hits) / 4,
                    "mrr": 1 / first if first else 0,
                    "ndcg_at_4": dcg / ideal if ideal else None,
                    "complete_evidence": bool(expected) and expected <= set(actual),
                    "correct_refusal": not expected and not actual,
                    "irrelevant_count": len(set(actual) - expected),
                }
            )

        def aggregate(group):
            positives = [r for r in group if r["positive"]]
            negatives = [r for r in group if not r["positive"]]
            return {
                "n": len(group),
                "positive_n": len(positives),
                "recall_at_4": sum(r["recall_at_4"] for r in positives) / len(positives)
                if positives
                else None,
                "precision_at_4": sum(r["precision_at_4"] for r in positives) / len(positives)
                if positives
                else None,
                "mrr": sum(r["mrr"] for r in positives) / len(positives) if positives else None,
                "ndcg_at_4": sum(r["ndcg_at_4"] for r in positives) / len(positives)
                if positives
                else None,
                "complete_evidence": sum(r["complete_evidence"] for r in positives),
                "negative_n": len(negatives),
                "correct_refusal": sum(r["correct_refusal"] for r in negatives),
                "irrelevant_sources": sum(r["irrelevant_count"] for r in group),
            }

        result[config["id"]] = {
            "overall": aggregate(rows),
            "by_kind": {
                kind: aggregate([r for r in rows if r["kind"] == kind])
                for kind in sorted({r["kind"] for r in rows})
            },
            "per_case": rows,
        }
    return result


def run(round_name, threshold=0.65, *, embed=False):
    if os.environ.get("RUN_LIVE_PHASE27") != "1":
        raise BudgetStop("phase27_explicit_opt_in_required")
    if not round_name.replace("-", "").isalnum():
        raise BudgetStop("phase27_invalid_round")
    value = corpus()
    settings = Settings().model_copy(
        update={
            "llm_provider": "mock",
            "llm_api_key": "",
            "langsmith_api_key": "",
            "datapilot_langsmith_enabled": False,
            "rag_evidence_policy": "legacy",
            "rag_min_similarity": 0.70,
            "rag_strategy": "vector",
            "rag_reranker": "none",
            "knowledge_requests_per_window": 120,
        }
    )
    if not settings.embedding_api_key or settings.embedding_model != "text-embedding-v4":
        raise BudgetStop("phase27_real_embedding_missing")
    output = ROOT / "docs/phase27" / round_name
    output.mkdir(parents=True, exist_ok=False)
    ledger = TrialLedger(LIVE / "ledger.sqlite", authorization())
    ledger.healthy()
    provider = CachedProvider(settings, ledger)
    estimator = InputEstimator(TOKENIZER)
    texts = sorted(
        {d["content"] for d in value["documents"]} | {q["query"] for q in value["queries"]}
    )
    missing = []
    for text in texts:
        try:
            ledger.cache(provider.space, [text])
        except BudgetStop as error:
            if str(error) != "trial_cache_miss_no_network_fallback":
                raise
            missing.append(text)
    batches = [missing[i : i + 10] for i in range(0, len(missing), 10)]
    reserve = sum(estimator.local.estimate(batch)[1] for batch in batches)
    prior = ledger.export()
    for stage, limits in ((None, LIMITS["embedding"]), (27, STAGE_LIMITS[27, "embedding"])):
        requests = [
            r
            for r in prior["requests"]
            if r["kind"] == "embedding" and (stage is None or r["stage"] == stage)
        ]
        if len(requests) + len(batches) > limits[0] or (
            sum(r["input_tokens"] for r in requests) + reserve > limits[1]
        ):
            raise BudgetStop("phase27_whole_plan_does_not_fit")
    configs = [
        {"id": "A", "strategy": "vector", "threshold": 0.70},
        {"id": "C", "strategy": "vector", "threshold": 0.70},
        {"id": "D", "strategy": "hybrid", "threshold": threshold},
    ]
    write_new(
        output / "plan.json",
        {
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
            "corpus_sha256": digest(FIXTURE),
            "configs": configs,
            "source_sha256": {p.name: digest(p) for p in (ROOT / "backend/app").glob("*.py")},
            "unique_texts": len(texts),
            "pending_batches": len(batches),
            "pending_token_reserve": reserve,
            "budget_before": prior,
            "chat": 0,
            "judge": 0,
            "langsmith": 0,
        },
    )
    base = engine_for_trial()
    try:
        if missing and not embed:
            raise BudgetStop("phase27_cache_missing_explicit_embed_required")
        for i, batch in enumerate(batches):
            transport = MeteredTransport(
                ledger,
                estimator,
                stage=27,
                kind="embedding",
                case="orchard-development",
                channel=f"batch-{i}",
                provider=settings.embedding_provider,
                model=provider.model,
                base_url=settings.embedding_base_url,
                space=provider.space,
            )
            CompatibleEmbeddingProvider(settings, transport=transport).embed(batch)
        with base.connect() as connection:
            before = counts(connection)
        records = []
        with temporary_database(base) as (engine, cleanup):
            with tempfile.TemporaryDirectory(dir=LIVE, prefix="phase27-") as directory:
                settings = settings.model_copy(update={"data_dir": Path(directory)})
                with (
                    patch("app.knowledge.get_embedding_provider", lambda _: provider),
                    patch("app.embeddings.get_embedding_provider", lambda _: provider),
                    tracing_context(enabled=False),
                    Session(engine, expire_on_commit=False) as session,
                ):
                    owner, targets, documents = seed(session, settings, value)
                    mapping = {v: k for k, v in documents.items()}
                    for question in value["queries"]:
                        row = {k: question[k] for k in ("id", "query", "kind", "relevant_ids")}
                        for config in configs:
                            current = settings.model_copy(
                                update={
                                    "rag_strategy": config["strategy"],
                                    "rag_min_similarity": config["threshold"],
                                    "rag_top_k": 4,
                                }
                            )
                            start = time.perf_counter()
                            result = retrieve_knowledge(
                                session, owner, targets["target"], question["query"], current
                            )
                            if result["status"] not in {"retrieved", "no_match"}:
                                raise BudgetStop("phase27_retrieval_error")
                            sources, decision = result["citations"], {}
                            raw = list(sources)
                            if config["id"] == "C":
                                registry = MetricRegistry(
                                    session, owner, targets["target"], current
                                )
                                sources, decision = select_knowledge(
                                    registry, question["query"], sources
                                )
                            elif config["id"] == "D":
                                sources, decision = select_prose(question["query"], sources)
                            # Verify through a fresh connection after committed API writes.
                            authenticity = []
                            with Session(engine) as independent:
                                for source in raw:
                                    chunk = independent.get(KnowledgeChunk, source["chunk_id"])
                                    doc = independent.get(KnowledgeDocument, source["document_id"])
                                    fixture = next(
                                        d for d in value["documents"] if d["id"] == mapping[doc.id]
                                    )
                                    valid = bool(
                                        chunk.document_id == doc.id
                                        and doc.owner_id == owner
                                        and doc.dataset_id == targets["target"]
                                        and doc.status == "ready"
                                        and doc.version == 1
                                        and chunk.content == doc.content == fixture["content"]
                                        and source["excerpt"] == chunk.content[:800]
                                        and chunk.embedding_space
                                        == doc.embedding_space
                                        == provider.space
                                        and chunk.embedding_model == provider.model
                                        and len(chunk.embedding) == 1536
                                    )
                                    authenticity.append(
                                        {
                                            "chunk_id": chunk.id,
                                            "version": doc.version,
                                            "valid": valid,
                                            "owner_id": doc.owner_id,
                                            "dataset_id": doc.dataset_id,
                                        }
                                    )
                            if not all(a["valid"] for a in authenticity):
                                raise BudgetStop("phase27_reference_or_authorization_failure")
                            row[config["id"]] = {
                                "ids": [mapping[s["document_id"]] for s in sources],
                                "sources": sources,
                                "raw_candidates": raw,
                                "decision": decision,
                                "authenticity": authenticity,
                                "stages": result.get("candidate_stages", []),
                                "latency_ms": round(1000 * (time.perf_counter() - start), 3),
                                "planned_source_ids": None,
                                "used_source_ids": None,
                            }
                        records.append(row)
                        write_new(output / f"{row['id']}.json", row)
                    write_new(
                        output / "database.json",
                        {
                            "documents": len(documents),
                            "owner": owner,
                            "targets": targets,
                            "mapping": mapping,
                            "independent_committed_reference_checks": True,
                        },
                    )
        with base.connect() as connection:
            after = counts(connection)
        assert before == after and cleanup["database_removed"]
        metrics = score(records, configs)
        d = metrics["D"]["overall"]
        eligible = d["recall_at_4"] >= 0.9 and d["correct_refusal"] / d["negative_n"] >= 0.9
        summary = {
            "status": "DEVELOPMENT_ELIGIBLE" if eligible else "DEVELOPMENT_LIMITED",
            "metrics": metrics,
            "cleanup": cleanup,
            "base_counts_unchanged": before == after,
            "ledger": ledger.export(),
            "permission_or_reference_errors": 0,
            "new_http": len(batches),
            "chat": 0,
            "judge": 0,
            "langsmith": 0,
            "final_generalization": "NOT_RUN",
            "agent_sql": "NOT_RUN",
        }
        write_new(output / "summary.json", summary)
        return {k: summary[k] for k in ("status", "new_http", "cleanup")}
    except Exception as failure:
        write_new(
            output / "failure.json", {"type": type(failure).__name__, "ledger": ledger.export()}
        )
        raise
    finally:
        ledger.close()
        base.dispose()
        app.dependency_overrides.clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", required=True)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--embed", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.round, args.threshold, embed=args.embed)))
    except Exception as failure:
        print(
            json.dumps(
                {
                    "status": "stopped",
                    "type": type(failure).__name__,
                    "reason": str(failure)
                    if isinstance(failure, BudgetStop)
                    else "details_suppressed",
                }
            )
        )
        raise SystemExit(2) from None
