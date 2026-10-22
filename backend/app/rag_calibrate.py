"""Development-only replay against PostgreSQL using frozen real embedding vectors."""

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import ROOT, Settings
from app.embeddings import CompatibleEmbeddingProvider, validate_vectors
from app.errors import AppError
from app.knowledge import retrieve_knowledge
from app.rag_evaluate import counts, load_corpus, seed
from app.rag_metrics import summarize_rag
from app.reranking import rerank

THRESHOLDS = (0.20, 0.40, 0.55, 0.70)
RERANK_THRESHOLDS = (0.10, 0.50, 0.80)


def configurations(flashrank):
    rows = [
        {
            "rag_strategy": strategy,
            "rag_reranker": "none",
            "rag_min_similarity": threshold,
            "reranker_min_score": 0.5,
        }
        for strategy in ("vector", "hybrid")
        for threshold in THRESHOLDS
    ]
    if flashrank:
        rows += [
            {
                "rag_strategy": "hybrid",
                "rag_reranker": "flashrank",
                "rag_min_similarity": threshold,
                "reranker_min_score": cutoff,
            }
            for threshold in THRESHOLDS
            for cutoff in RERANK_THRESHOLDS
        ]
    return rows


def calibrate(url, cache_path, output, flashrank):
    corpus, hashes = load_corpus()
    cache = json.loads(cache_path.read_bytes())
    original = Settings()
    if not (make_url(url).database or "").endswith("_test"):
        raise ValueError("Independent _test database required")
    if (
        cache["corpus"]["sha256"] != hashes["sha256"]
        or cache["configuration"]["embedding_model"] != original.embedding_model
    ):
        raise ValueError("Real vector cache identity mismatch")
    # No holdout queries, rankings or scores are read for selecting configuration.
    cases = [c for c in corpus["queries"] if c["split"] == "development"]
    report = {
        "corpus": hashes,
        "mode": "cached-real-vectors-PG-no-embedding-HTTP",
        "objective": "maximize (positive Recall@4 + no-answer recall)/2; then MRR; "
        "then prefer vector/none for equal quality; no holdout selection",
        "predeclared_thresholds": THRESHOLDS,
        "reranker_cutoffs": RERANK_THRESHOLDS,
        "records": [],
    }
    rank_cache, cold_ms = {}, []

    def measured_rerank(question, passages, configured):
        if configured.rag_reranker == "none" or not passages:
            return rerank(question, passages, configured)
        identity = (question, tuple(p["chunk_id"] for p in passages))
        if identity not in rank_cache:
            start = time.perf_counter()
            rank_cache[identity] = rerank(
                question, passages, configured.model_copy(update={"reranker_min_score": 0.0})
            )
            cold_ms.append(round((time.perf_counter() - start) * 1000, 3))
        return [
            p for p in rank_cache[identity] if p["reranker_score"] >= configured.reranker_min_score
        ]

    output.parent.mkdir(parents=True, exist_ok=True)
    output.touch(exist_ok=False)
    engine = create_engine(url)
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / "data/runtime", prefix="rag-matrix-") as temp:
            settings = original.model_copy(
                update={"data_dir": Path(temp), "datapilot_langsmith_enabled": False}
            )

            def embedded(provider, texts):
                if any(t not in cache["vectors"] for t in texts):
                    raise ValueError("Missing cached real vector")
                return validate_vectors([cache["vectors"][t] for t in texts], len(texts))

            with engine.connect() as connection:
                before = counts(connection)
                connection.rollback()
                transaction = connection.begin()
                try:
                    with Session(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    ) as session:
                        with (
                            patch.object(CompatibleEmbeddingProvider, "embed", embedded),
                            patch("app.retrieval.rerank", measured_rerank),
                        ):
                            seed(session, corpus, settings)
                            for config in configurations(flashrank):
                                measured = []
                                configured = settings.model_copy(update=config)
                                for case in cases:
                                    start = time.perf_counter()
                                    try:
                                        found = retrieve_knowledge(
                                            session,
                                            corpus["owners"][case["owner"]],
                                            corpus["datasets"][case["dataset"]]["id"],
                                            case["query"],
                                            configured,
                                        )
                                    except AppError as error:
                                        found = {
                                            "status": "denied"
                                            if error.status_code == 404
                                            else "error",
                                            "citations": [],
                                        }
                                    authorized = [
                                        d["chunk_id"]
                                        for d in corpus["documents"]
                                        if d["owner"] == case["owner"]
                                        and (d["personal"] or d["dataset"] == case["dataset"])
                                    ]
                                    measured.append(
                                        {
                                            "id": case["id"],
                                            "retrieval_status": found["status"],
                                            "retrieved_ids": [
                                                c["chunk_id"] for c in found["citations"]
                                            ],
                                            "authorized_chunk_ids": authorized,
                                            "retrieval_ms": round(
                                                (time.perf_counter() - start) * 1000, 3
                                            ),
                                        }
                                    )
                                summary = summarize_rag(cases, measured)
                                report["records"].append(
                                    {"config": config, "summary": summary, "records": measured}
                                )
                                output.write_text(json.dumps(report, indent=2), encoding="utf-8")
                finally:
                    transaction.rollback()
                report["rollback_verified"] = counts(connection) == before
    finally:
        engine.dispose()
    eligible = [
        r
        for r in report["records"]
        if r["summary"]["retrieval_errors"] == 0
        and r["summary"]["cross_permission_leak_count"] == 0
    ]

    def key(row):
        s, c = row["summary"], row["config"]
        return (
            (s["retrieval_hit_rate"] + s["no_answer_recall"]) / 2,
            s["ranking"]["mrr"],
            c["rag_reranker"] == "none",
            c["rag_strategy"] == "vector",
        )

    report["selected"] = max(eligible, key=key)["config"]
    report["reranker_measurement"] = {
        "cold_process_calls": len(cold_ms),
        "cold_process_ms": cold_ms,
        "note": "Identical query/candidate sets reuse actual raw reranker scores across cutoffs; "
        "warm matrix latency is not production cold-start latency",
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--flashrank", action="store_true")
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args()
    report = calibrate(os.environ["EVAL_DATABASE_URL"], args.cache, args.output, args.flashrank)
    if args.freeze:
        if not args.flashrank or any(r["summary"]["retrieval_errors"] for r in report["records"]):
            raise SystemExit("Cannot freeze before all three strategy comparisons succeed")
        with args.freeze.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "corpus_sha256": report["corpus"]["sha256"],
                    "selected": report["selected"],
                    "baseline": configurations(False)[0],
                    "agent_source_sha256": hashlib.sha256(
                        (Path(__file__).parent / "analysis_agent.py").read_bytes()
                    ).hexdigest(),
                    "matrix_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                },
                stream,
                indent=2,
            )
    print(json.dumps({"selected": report["selected"], "comparisons": len(report["records"])}))


if __name__ == "__main__":
    main()
