"""Stage18 independent reviewed retrieval experiment; no chat calls or default changes."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import ROOT, Settings
from app.embeddings import CompatibleEmbeddingProvider, get_embedding_provider
from app.errors import AppError
from app.evaluate import FIXTURES
from app.knowledge import retrieve_knowledge
from app.models import KnowledgeChunk, KnowledgeDocument
from app.rag_evaluate import EmbeddingObservation, counts, seed
from app.rag_metrics import summarize_rag

SOURCES = (
    "rag18_evaluate.py",
    "knowledge.py",
    "retrieval.py",
    "reranking.py",
    "embeddings.py",
    "config.py",
    "tracing.py",
    "analysis_agent.py",
    "analysis_graph.py",
    "providers.py",
)
BASELINE = {"rag_strategy": "vector", "rag_reranker": "none", "rag_min_similarity": 0.7}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def reviewed():
    receipt = json.loads((FIXTURES / "rag18-v1.review.json").read_bytes())
    if receipt["status"] != "human-confirmed":
        raise ValueError("Human confirmation required")
    for name, expected in receipt["sha256"].items():
        if name not in {"rag18-v1.json", "rag18-support.csv", "rag18-delivery.csv"}:
            raise ValueError("Invalid reviewed file")
        if sha(FIXTURES / name) != expected:
            raise ValueError("Reviewed fixture hash mismatch")
    corpus = json.loads((FIXTURES / "rag18-v1.json").read_bytes())
    groups = [
        {c["family"] for c in corpus["queries"] if c["split"] == split}
        for split in ("development", "holdout")
    ]
    if groups[0] & groups[1]:
        raise ValueError("Intent groups overlap")
    return corpus, receipt


def configurations():
    return [
        {"rag_strategy": strategy, "rag_reranker": reranker, "rag_min_similarity": threshold}
        for threshold in (0.65, 0.7)
        for strategy, reranker in (("vector", "none"), ("hybrid", "none"), ("hybrid", "flashrank"))
    ]


def choose(profiles):
    eligible = [
        p
        for p in profiles
        if p["summary"]["cross_permission_leak_count"] == 0
        and p["summary"]["retrieval_errors"] == 0
    ]
    if not eligible:
        raise ValueError("No valid candidate configuration")

    def score(p):
        summary, config = p["summary"], p["configuration"]
        return (
            (summary["ranking"]["recall_at_k"] + summary["no_answer_recall"]) / 2,
            summary["ranking"]["mrr"],
            config["rag_reranker"] == "none",
            config["rag_strategy"] == "vector",
            config["rag_min_similarity"] == 0.7,
        )

    return max(eligible, key=score)["configuration"]


def fingerprint(settings):
    return {
        "implementation_sha256": {name: sha(Path(__file__).parent / name) for name in SOURCES},
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "embedding_space_sha256": hashlib.sha256(
            get_embedding_provider(settings).space.encode()
        ).hexdigest(),
        "flashrank_model_sha256": sha(
            settings.reranker_cache_dir
            / "ms-marco-MultiBERT-L-12"
            / "flashrank-MultiBERT-L12_Q.onnx"
        ),
        "top_k": 4,
        "reranker_min_score": 0.5,
        "rrf_constant": 60,
    }


def evaluate(split, output, database_url, freeze=None):
    original = Settings()
    if original.embedding_provider in {"none", "fake"} or not original.embedding_api_key:
        raise ValueError("Explicit real embedding configuration required")
    if make_url(database_url).database != "datapilot_test":
        raise ValueError("Independent datapilot_test database required")
    corpus, receipt = reviewed()
    current = fingerprint(original)
    if split == "holdout":
        frozen = json.loads(freeze.read_bytes())
        if frozen["fingerprint"] != current or frozen["review"] != receipt:
            raise ValueError("Frozen implementation, model or reviewed corpus changed")
        configs = [BASELINE] + ([] if frozen["selected"] == BASELINE else [frozen["selected"]])
    else:
        configs = configurations()
    write(output, {"state": "claimed", "split": split}, exclusive=True)
    # Exclusive claim remains after failures; a formal holdout is never automatically repeated.
    cases = [c for c in corpus["queries"] if c["split"] == split]
    prefix = "support" if split == "development" else "delivery"
    subset = {
        **corpus,
        "datasets": {k: v for k, v in corpus["datasets"].items() if k.startswith(prefix)},
        "documents": [d for d in corpus["documents"] if d["dataset"].startswith(prefix)],
    }
    observation = EmbeddingObservation()
    report = {
        "state": "running",
        "split": split,
        "review": receipt,
        "fingerprint": current,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": datetime.now(UTC).isoformat(),
        "profiles": [],
        "chat_requests": 0,
        "embedding_provider": original.embedding_provider,
        "embedding_model": original.embedding_model,
        "embedding_dimensions": original.embedding_dimensions,
        "cost": "unknown",
    }
    engine = create_engine(database_url, connect_args={"connect_timeout": 5})
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / "data/runtime", prefix="rag18-") as temp:
            settings = original.model_copy(
                update={
                    "data_dir": Path(temp),
                    "datapilot_langsmith_enabled": False,
                    "reranker_python": ROOT / "data/runtime/eval-venv/Scripts/python.exe",
                    "reranker_timeout_seconds": 60,
                    "rag_top_k": 4,
                    "reranker_min_score": 0.5,
                }
            )
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
                        with patch.object(
                            CompatibleEmbeddingProvider,
                            "embed",
                            lambda provider, texts: observation.embed(provider, texts),
                        ):
                            seed(session, subset, settings)
                            # Query vectors come from this real provider, collected once then reused
                            # identically across configurations. No canned or fake vectors.
                            provider = get_embedding_provider(settings)
                            questions = list(
                                dict.fromkeys(c["query"] for c in cases if c["authorized"])
                            )
                            for offset in range(0, len(questions), 8):
                                provider.embed(questions[offset : offset + 8])
                        cached = EmbeddingObservation(cached=observation.vectors)
                        with patch.object(
                            CompatibleEmbeddingProvider,
                            "embed",
                            lambda provider, texts: cached.embed(provider, texts),
                        ):
                            for config in configs:
                                configured = settings.model_copy(update=config)
                                records = []
                                for case in cases:
                                    authorized = [
                                        d["chunk_id"]
                                        for d in subset["documents"]
                                        if d["owner"] == case["owner"]
                                        and d["dataset"] == case["dataset"]
                                    ]
                                    start, stamp = time.perf_counter(), datetime.now(UTC)
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
                                            "error_code": error.detail["code"],
                                        }
                                    for citation in found["citations"]:
                                        chunk = session.get(KnowledgeChunk, citation["chunk_id"])
                                        document = session.get(
                                            KnowledgeDocument, citation["document_id"]
                                        )
                                        if (
                                            not chunk
                                            or not document
                                            or chunk.document_id != document.id
                                        ):
                                            raise ValueError(
                                                "Citation does not resolve to persisted knowledge"
                                            )
                                        if citation["excerpt"] != chunk.content[:800]:
                                            raise ValueError("Citation body mismatch")
                                    records.append(
                                        {
                                            "id": case["id"],
                                            "started_at": stamp.isoformat(),
                                            "ended_at": datetime.now(UTC).isoformat(),
                                            "retrieval_status": found["status"],
                                            "error_code": found.get("error_code"),
                                            "retrieval_ms": round(
                                                (time.perf_counter() - start) * 1000, 3
                                            ),
                                            "authorized_chunk_ids": authorized,
                                            "retrieved_ids": [
                                                c["chunk_id"] for c in found["citations"]
                                            ],
                                            "citations": found["citations"],
                                            "candidate_stages": found.get("candidate_stages", []),
                                        }
                                    )
                                report["profiles"].append(
                                    {
                                        "configuration": config,
                                        "records": records,
                                        "summary": summarize_rag(cases, records),
                                        "by_category": {
                                            category: summarize_rag(
                                                [c for c in cases if c["type"] == category],
                                                [
                                                    r
                                                    for r in records
                                                    if next(c for c in cases if c["id"] == r["id"])[
                                                        "type"
                                                    ]
                                                    == category
                                                ],
                                            )
                                            for category in sorted({c["type"] for c in cases})
                                        },
                                    }
                                )
                                write(output, report)
                finally:
                    transaction.rollback()
                report["database_rollback_verified"] = counts(connection) == before
                if not report["database_rollback_verified"]:
                    raise ValueError("Rollback count mismatch")
        if split == "development":
            report["selected"] = choose(report["profiles"])
        report["state"] = "complete"
    except Exception as error:
        report["state"], report["failure_class"] = "failed", type(error).__name__
        raise
    finally:
        report["embedding_requests"] = observation.requests
        report["ended_at"] = datetime.now(UTC).isoformat()
        write(output, report)
        engine.dispose()
    if split == "development":
        write(
            freeze,
            {
                "fingerprint": current,
                "review": receipt,
                "selected": report["selected"],
                "baseline": BASELINE,
                "development_sha256": sha(output),
                "holdout_protocol": "One batch, baseline and selected only; no further tuning",
            },
            exclusive=True,
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--split", choices=["development", "holdout"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    args = parser.parse_args()
    if not args.live or not os.environ.get("DATAPILOT_RUN_REAL_EMBEDDING") == "1":
        parser.error("Both explicit live opt-ins required")
    url = make_url(Settings().database_url).set(host="127.0.0.1", database="datapilot_test")
    try:
        report = evaluate(
            args.split, args.output, url.render_as_string(hide_password=False), args.freeze
        )
    except Exception as error:
        print(json.dumps({"state": "failed", "failure_class": type(error).__name__}))
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "state": report["state"],
                "split": args.split,
                "profiles": len(report["profiles"]),
                "requests": len(report["embedding_requests"]),
            }
        )
    )


if __name__ == "__main__":
    main()
