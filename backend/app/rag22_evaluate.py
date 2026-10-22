"""Development-only RAG comparison. No chat, cloud telemetry or history rewrites."""

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import ROOT, Settings
from app.embeddings import FakeEmbeddingProvider
from app.errors import AppError
from app.knowledge import retrieve_knowledge
from app.metric21_acceptance import check_prior_usage
from app.metric_resolution import MetricRegistry
from app.models import KnowledgeChunk, KnowledgeDocument
from app.rag_evaluate import counts, seed
from app.rag_metrics import rank_scores
from app.retrieval_evidence import assess_evidence

FIXTURE = Path(__file__).parent / "evals/rag22"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture(require_review=True):
    freeze = json.loads((FIXTURE / "freeze-v1.json").read_bytes())
    expected = {"corpus-v1.json", "contract-v1.json", "library.csv"}
    if set(freeze["sha256"]) != expected:
        raise ValueError("Unexpected freeze manifest")
    if any(sha(FIXTURE / name) != value for name, value in freeze["sha256"].items()):
        raise ValueError("Frozen fixture changed; create an explicitly reviewed new version")
    if require_review:
        receipt = json.loads((FIXTURE / "review-v1.json").read_bytes())
        if receipt["status"] != "human-confirmed" or receipt["sha256"] != freeze["sha256"]:
            raise ValueError("Human review bound to this fixture is required")
    corpus = json.loads((FIXTURE / "corpus-v1.json").read_bytes())
    ids = {d["id"] for d in corpus["documents"]}
    if len(ids) != len(corpus["documents"]) or len({q["id"] for q in corpus["queries"]}) != len(
        corpus["queries"]
    ):
        raise ValueError("Duplicate IDs")
    for query in corpus["queries"]:
        if not set(query["relevant_ids"]) <= ids:
            raise ValueError("Unknown relevance label")
        if bool(query["relevant_ids"]) != (query["expected_evidence"] == "resolved"):
            raise ValueError("Conflicting evidence label")
    return corpus, freeze


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


@contextmanager
def cached_fake_queries(queries):
    """Offline only: exclude vector generation from the frozen latency contract.

    Cache misses fail. No implicit external service, fallback, or real-vector claim.
    """
    vectors = FakeEmbeddingProvider().embed(queries)
    cache = dict(zip(queries, vectors, strict=True))
    with patch.object(FakeEmbeddingProvider, "embed", lambda _p, texts: [cache[t] for t in texts]):
        yield {"query_vectors_precomputed": True, "unique_query_vectors": len(cache)}


def summarize(cases, records, list_name):
    if len(records) != len(cases) or {r["id"] for r in records} != {c["id"] for c in cases}:
        raise ValueError("Every case must have exactly one record")
    by_id = {r["id"]: r for r in records}
    positive, negative, applicable, citations = [], [], [], []
    leaks, denials, malicious = 0, 0, 0
    latencies = []
    for case in cases:
        row = by_id[case["id"]]
        ids = row[list_name]
        leaks += len(set(ids) - set(row["authorized_ids"]))
        malicious += len(set(ids) & set(row["malicious_ids"]))
        citations.extend(row[list_name + "_authenticity"])
        if case["expected_evidence"] == "denied":
            denials += row["candidate_status"] == "denied" and not ids
            continue
        latencies.append(
            row["retrieval_ms"] + (row["filter_ms"] if list_name == "evidence_ids" else 0)
        )
        if case["relevant_ids"]:
            positive.append(rank_scores(ids, case["relevant_ids"], 4))
        else:
            # A failed request is never a correct no-match.
            negative.append((not ids and row["candidate_status"] != "error", bool(ids)))
        predicted = (
            "error"
            if row["candidate_status"] == "error"
            else row["evidence_status"]
            if list_name == "evidence_ids"
            else ("resolved" if ids else "not_found")
        )
        applicable.append(predicted == case["expected_evidence"])

    def mean(values):
        return statistics.mean(values) if values else None

    return {
        "cases": len(cases),
        "positive_denominator": len(positive),
        "ranking": {key: mean([r[key] for r in positive]) for key in rank_scores([], [], 4)},
        "negative_denominator": len(negative),
        "no_match_recall": mean([int(r[0]) for r in negative]),
        "false_retrieval_rate": mean([int(r[1]) for r in negative]),
        "applicability_denominator": len(applicable),
        "applicability_accuracy": mean(applicable),
        "citation_denominator": len(citations),
        "citation_authenticity": mean(citations),
        "permission_denials": denials,
        "cross_permission_leaks": leaks,
        "malicious_source_occurrences": malicious,
        "agent_selected_sources": None,
        "agent_used_sources": None,
        "latency_p50_ms": statistics.median(latencies) if latencies else None,
        "latency_p95_ms": percentile(latencies, 0.95),
    }


def synthetic_seed(corpus):
    owners = {name: str(uuid4()) for name in ("reader", "other")}
    datasets = {
        name: {"id": str(uuid4()), "owner": "reader", "fixture": "rag22/library.csv"}
        for name in ("target", "sibling")
    }
    docs = [
        {
            **doc,
            "dataset": "target" if doc["scope"] == "personal" else doc["scope"],
            "personal": doc["scope"] == "personal",
            "document_id": str(uuid5(NAMESPACE_URL, "datapilot-rag22-v1/doc/" + doc["id"])),
            "chunk_id": str(uuid5(NAMESPACE_URL, "datapilot-rag22-v1/chunk/" + doc["id"])),
        }
        for doc in corpus["documents"]
    ]
    return {"owners": owners, "datasets": datasets, "documents": docs}


def compare(session, corpus, seeded, settings):
    mapping = {d["chunk_id"]: d["id"] for d in seeded["documents"]}
    malicious = [d["id"] for d in seeded["documents"] if d["id"].startswith("inject_")]
    records = []
    for case in corpus["queries"]:
        owner, target = seeded["owners"][case["owner"]], seeded["datasets"][case["dataset"]]["id"]
        authorized = [
            d["id"]
            for d in seeded["documents"]
            if d["owner"] == case["owner"] and d["scope"] in {case["dataset"], "personal"}
        ]
        if seeded["datasets"][case["dataset"]]["owner"] != case["owner"]:
            authorized = []
        record = {
            "id": case["id"],
            "candidate_ids": [],
            "evidence_ids": [],
            "candidate_ids_authenticity": [],
            "evidence_ids_authenticity": [],
            "authorized_ids": authorized,
            "malicious_ids": malicious,
            "retrieval_ms": 0,
            "filter_ms": 0,
        }
        started = time.perf_counter()
        try:
            result = retrieve_knowledge(session, owner, target, case["query"], settings)
            record["retrieval_ms"] = round((time.perf_counter() - started) * 1000, 3)
            if result["status"] == "error":
                raise ValueError(
                    "Retrieval error; stop rather than score service failure as no-match"
                )
            started = time.perf_counter()
            registry = MetricRegistry(session, owner, target, settings)
            evidence, summary = assess_evidence(registry, case["query"], result["citations"])
            record["filter_ms"] = round((time.perf_counter() - started) * 1000, 3)
            record.update(
                candidate_status=result["status"],
                evidence_status=summary["status"],
                stages=result.get("candidate_stages", []),
                evidence=summary,
            )
            for name, sources in (
                ("candidate_ids", result["citations"]),
                ("evidence_ids", evidence),
            ):
                record[name] = [mapping.get(s["chunk_id"], "UNKNOWN") for s in sources]
                for source in sources:
                    chunk = session.get(KnowledgeChunk, source["chunk_id"])
                    document = session.get(KnowledgeDocument, source["document_id"])
                    record[name + "_authenticity"].append(
                        bool(
                            chunk
                            and document
                            and chunk.document_id == document.id
                            and source["excerpt"] == chunk.content[:800]
                            and source["title"] == document.title
                        )
                    )
        except AppError as error:
            if error.status_code != 404:
                raise
            record.update(candidate_status="denied", evidence_status="denied")
        records.append(record)
    return records


def evaluate(mode, output):
    # Claim before doing work; never overwrite an earlier report or checkpoint.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump({"state": "claimed", "mode": mode}, stream)
    report = {
        "state": "running",
        "mode": mode,
        "chat_requests": 0,
        "cloud_tracing": False,
        "semantic_embedding_verified": False,
        "embedding_requests": 0,
        "embedding_tokens": 0 if mode == "offline" else None,
        "cost": None,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "sources_sha256": {
            name: sha(Path(__file__).parent / name)
            for name in (
                "rag22_evaluate.py",
                "retrieval_evidence.py",
                "metric_resolution.py",
                "knowledge.py",
                "retrieval.py",
                "config.py",
                "analyses.py",
                "analysis_graph.py",
                "analysis_agent.py",
                "sql_guard.py",
                "tracing.py",
            )
        },
        "configuration": {"strategy": "vector", "similarity": 0.70, "reranker": "none", "k": 4},
    }
    try:
        if mode == "live":
            # Shared existing fixed-path guard; output names do NOT reset usage.
            check_prior_usage(ROOT / "docs")
            if os.environ.get("RUN_LIVE_RAG22") != "1":
                raise ValueError("Explicit live opt-in required")
            # No live execution until a separate bounded ledger is implemented
            # after the prior uncertain usage is resolved. Never fall back to Fake.
            raise ValueError("Live RAG22 budget ledger not authorized; no external request issued")
        corpus, freeze = load_fixture()
        original = Settings()
        url = make_url(original.database_url).set(host="127.0.0.1", database="datapilot_test")
        engine = create_engine(url, connect_args={"connect_timeout": 5})
        try:
            with tempfile.TemporaryDirectory(
                dir=ROOT / "data/runtime", prefix="rag22-"
            ) as directory:
                settings = original.model_copy(
                    update={
                        "data_dir": Path(directory),
                        "embedding_provider": "fake",
                        "embedding_api_key": "",
                        "llm_api_key": "",
                        "langsmith_api_key": "",
                        "datapilot_langsmith_enabled": False,
                        "rag_strategy": "vector",
                        "rag_min_similarity": 0.70,
                        "rag_reranker": "none",
                        "rag_top_k": 4,
                        "rag_evidence_policy": "legacy",
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
                            seeded = synthetic_seed(corpus)
                            seed(session, seeded, settings)
                            with cached_fake_queries(
                                [q["query"] for q in corpus["queries"]]
                            ) as cache:
                                record = compare(session, corpus, seeded, settings)
                            persisted = session.scalars(
                                select(KnowledgeChunk).where(
                                    KnowledgeChunk.document_id.in_(
                                        [d["document_id"] for d in seeded["documents"]]
                                    )
                                )
                            ).all()
                            report.update(
                                vector_cache=cache,
                                persisted_vector_count=len(persisted),
                                persisted_vector_dimensions=sorted(
                                    {len(c.embedding) for c in persisted}
                                ),
                                records=record,
                                freeze=freeze,
                                vector_backend="PostgreSQL/pgvector",
                                embedding="fake-lexical-hash-v1",
                                vector_dimensions=1536,
                                postgres_version=connection.scalar(text("SHOW server_version")),
                                vector_version=connection.scalar(
                                    text(
                                        "SELECT extversion FROM pg_extension WHERE extname='vector'"
                                    )
                                ),
                                baseline=summarize(corpus["queries"], record, "candidate_ids"),
                                registered=summarize(corpus["queries"], record, "evidence_ids"),
                            )
                    finally:
                        transaction.rollback()
                    report["rollback_counts_equal"] = counts(connection) == before
        finally:
            engine.dispose()
        report["state"] = "complete"
    except (ValueError, FileNotFoundError) as error:
        # Even ValidationError may contain private endpoint input values.
        # Only a single known guard message is safe to serialize verbatim.
        known_guard = "Prior uncertain usage blocks additional live calls"
        report.update(
            state="blocked",
            reason=known_guard
            if str(error) == known_guard
            else "Fixture, review or live configuration validation blocked evaluation",
        )
    except Exception as error:
        report.update(
            state="error",
            reason="Evaluation failed; external exception bodies are not exported",
            failure_type=type(error).__name__,
        )
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "live"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.mode, args.output)
    print(
        json.dumps({k: result[k] for k in ("state", "mode", "chat_requests", "embedding_requests")})
    )
    raise SystemExit(0 if result["state"] == "complete" else 2)


if __name__ == "__main__":
    main()
