"""Reviewed synthetic RAG acceptance; live calls require two explicit opt-ins."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth import COOKIE, token_hash
from app.config import ROOT, Settings, get_settings
from app.datasets import dataset_path
from app.db import Base, get_session
from app.embeddings import CompatibleEmbeddingProvider, get_embedding_provider, validate_vectors
from app.errors import AppError
from app.evaluate import FIXTURES, case_evidence, dependency
from app.ingestion import import_dataset
from app.knowledge import KnowledgeInput, content_hash, retrieve_knowledge
from app.main import app
from app.models import AuthSession, Dataset, KnowledgeChunk, KnowledgeDocument, User
from app.providers import CompatibleProvider, get_provider
from app.rag_metrics import summarize_rag


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def load_corpus():
    raw = (FIXTURES / "rag-quality-v1.json").read_bytes()
    corpus = json.loads(raw)
    receipt = json.loads((FIXTURES / "rag-quality-v1.review.json").read_bytes())
    sha = hashlib.sha256(raw).hexdigest()
    if receipt["status"] != "human-confirmed" or receipt["dataset_sha256"] != sha:
        raise ValueError("Human review must bind the exact corpus SHA before evaluation")
    cases = corpus["queries"]
    documents = corpus["documents"]
    ids = {d["chunk_id"] for d in documents}
    if len(ids) != len(documents) or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate source or query IDs")
    groups = {}
    for split in ("development", "holdout"):
        rows = [c for c in cases if c["split"] == split]
        groups[split] = {" ".join(c["query"].casefold().split()) for c in rows}
    if groups["development"] & groups["holdout"]:
        raise ValueError("Queries overlap across splits")
    for case in cases:
        if not set(case["relevant_chunk_ids"]) <= ids:
            raise ValueError("Unknown relevant source")
        if any(v not in (0, 1) for v in case["relevance_grades"].values()):
            raise ValueError("Only reviewed binary relevance is supported")
    return corpus, {
        "sha256": sha,
        "knowledge_sha256": digest(documents),
        "queries_sha256": digest([{k: c[k] for k in ("id", "query", "split")} for c in cases]),
        "labels_sha256": digest([{k: v for k, v in c.items() if k != "query"} for c in cases]),
        "review_status": receipt["status"],
    }


class EmbeddingObservation:
    """Instrument the original real HTTP provider; never supply canned responses."""

    def __init__(self, cached=None):
        self.requests, self.vectors = [], {}
        self.cached = cached
        self.original = CompatibleEmbeddingProvider.embed

    def embed(self, provider, texts):
        if self.cached is not None:
            if any(text not in self.cached for text in texts):
                raise ValueError("Missing real cached vector; no implicit network fallback")
            self.vectors.update({text: self.cached[text] for text in texts})
            return validate_vectors([self.cached[text] for text in texts], len(texts))
        if provider.transport is not None or len(self.requests) >= 120:
            raise ValueError("Live evaluation forbids injected transports and unbounded requests")
        start = time.perf_counter()
        record = {"inputs": len(texts), "status": "error"}
        try:
            vectors = self.original(provider, texts)
            self.vectors.update(zip(texts, vectors, strict=True))
            record["status"] = "success"
            record["usage"] = provider.usage.model_dump()
            return vectors
        except AppError as error:
            record["code"] = error.detail["code"]
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - start) * 1000, 3)
            self.requests.append(record)


def counts(connection):
    return {
        table.name: connection.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }


def seed(session, corpus, settings):
    for label, owner_id in corpus["owners"].items():
        if session.get(User, owner_id):
            raise ValueError("Synthetic ID collision; no existing records will be overwritten")
        session.add(User(id=owner_id, username="rag-" + label, password_hash="unused-fixture"))
    session.flush()
    for item in corpus["datasets"].values():
        path = dataset_path(settings, item["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        profile, sheet = import_dataset(
            (FIXTURES / item["fixture"]).read_bytes(), ".csv", None, path, settings
        )
        session.add(
            Dataset(
                id=item["id"],
                owner_id=corpus["owners"][item["owner"]],
                name="synthetic.csv",
                file_type="csv",
                sheet_name=sheet,
                row_count=profile["row_count"],
                column_count=profile["column_count"],
                profile=profile,
            )
        )
    session.flush()
    provider = get_embedding_provider(settings)
    documents = corpus["documents"]
    vectors = []
    for offset in range(0, len(documents), 8):
        texts = [d["content"] for d in documents[offset : offset + 8]]
        vectors.extend(validate_vectors(provider.embed(texts), len(texts)))
    for item, vector in zip(documents, vectors, strict=True):
        dataset_id = None if item["personal"] else corpus["datasets"][item["dataset"]]["id"]
        body = KnowledgeInput(
            title=item["title"], source_type=item["source_type"], content=item["content"]
        )
        session.add(
            KnowledgeDocument(
                id=item["document_id"],
                owner_id=corpus["owners"][item["owner"]],
                dataset_id=dataset_id,
                scope_key=dataset_id or "personal",
                title=body.title,
                source_type=body.source_type,
                content=body.content,
                content_hash=content_hash(body),
                status="ready",
                embedding_space=provider.space,
            )
        )
        session.flush()
        session.add(
            KnowledgeChunk(
                id=item["chunk_id"],
                document_id=item["document_id"],
                chunk_index=0,
                content=item["content"],
                embedding=vector,
                embedding_space=provider.space,
                embedding_model=provider.model,
                details={"synthetic": True},
            )
        )
    session.commit()


def downstream(client, session, corpus, case, settings, retrieval):
    # A fresh provider avoids carrying previous request usage into clarification-only cases.
    app.dependency_overrides[get_provider] = dependency(CompatibleProvider(settings))
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=corpus["owners"][case["owner"]],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    session.commit()
    client.cookies.set(COOKIE, token)
    target = corpus["datasets"][case["dataset"]]["id"]
    response = client.post(f"/api/datasets/{target}/analyses", json={"question": case["query"]})
    body = response.json()
    if response.status_code >= 400:
        failure = body.get("detail", {})
        body = (
            client.get(f"/api/analyses/{failure['analysis_id']}").json()
            if failure.get("analysis_id")
            else {"error_code": failure.get("code"), "status": "error"}
        )
    workflow = body.get("telemetry", {}).get("workflow", {})
    evidence = case_evidence(body, workflow.get("rag", retrieval))
    evidence.update(
        sql_model_calls=workflow.get("sql_model_calls"),
        status=body.get("status"),
        error_code=body.get("error_code"),
        adopted_ids=workflow.get("plan", {}).get("knowledge_chunk_ids", []),
        cited_ids=[c["chunk_id"] for c in workflow.get("rag", {}).get("citations", [])],
        agent=workflow.get("agent", {}),
        http_status=response.status_code,
    )
    behavior = case["downstream"]["behavior"]
    evidence["behavior_correct"] = (
        body.get("status") == "clarification"
        if behavior == "clarification"
        else "execute" not in workflow.get("nodes", [])
        if behavior == "safe_refusal"
        else body.get("status") == "success"
    )
    if workflow.get("sql_model_calls") == 0:
        evidence["usage"]["sql"] = {"source": "not_called", "total_tokens": 0}
    return evidence


def evaluate(
    database_url,
    split,
    output,
    *,
    run_downstream=False,
    cache=None,
    updates=None,
    cached=None,
    case_ids=None,
):
    if os.environ.get("RUN_LIVE_RAG_EVAL") != "1":
        raise ValueError("Live acceptance requires RUN_LIVE_RAG_EVAL=1 and --live")
    original = Settings()
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        raise ValueError("Use an independent migrated PostgreSQL database ending in _test")
    if url.database == make_url(original.database_url).database:
        raise ValueError("Business database is forbidden")
    if not original.embedding_api_key or original.embedding_provider not in {
        "openai",
        "openai_compatible",
    }:
        raise ValueError("Explicit real Embedding configuration is required")
    if run_downstream and (not original.llm_api_key or original.llm_provider == "mock"):
        raise ValueError("Explicit real chat configuration is required")
    corpus, hashes = load_corpus()
    cases = [
        c
        for c in corpus["queries"]
        if c["split"] == split and (case_ids is None or c["id"] in case_ids)
    ]
    if not cases or (case_ids is not None and {c["id"] for c in cases} != set(case_ids)):
        raise ValueError("Unknown evaluation case IDs")
    observation = EmbeddingObservation(cached)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    report = {
        "mode": "real-embedding-real-agent" if run_downstream else "real-embedding",
        "created_at": datetime.now(UTC).isoformat(),
        "head": head,
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"])),
        "split": split,
        "corpus": hashes,
        "records": [],
        "embedding_requests": observation.requests,
        "embedding_tokens": None,
        "embedding_transport": "cached-real-vectors-no-HTTP" if cached else "real-HTTP",
        "cost": None,
        "source_sha256": {
            p: hashlib.sha256((ROOT / "backend/app" / p).read_bytes()).hexdigest()
            for p in (
                "knowledge.py",
                "embeddings.py",
                "analysis_agent.py",
                "analysis_graph.py",
                "providers.py",
                "retrieval.py",
                "reranking.py",
                "config.py",
                "rag_evaluate.py",
            )
        },
    }
    # Exclusive output protects historical measurements from accidental replacement.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.touch(exist_ok=False)
    engine = create_engine(database_url)
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / "data/runtime", prefix="rag-eval-") as temp:
            settings = original.model_copy(
                update={
                    "data_dir": Path(temp),
                    "datapilot_langsmith_enabled": False,
                    "agent_native_tools": True,
                    "analysis_requests_per_window": 1000,
                    "knowledge_requests_per_window": 120,
                    **(updates or {}),
                }
            )
            report["configuration"] = {
                k: getattr(settings, k)
                for k in (
                    "embedding_provider",
                    "embedding_model",
                    "embedding_dimensions",
                    "llm_provider",
                    "llm_model",
                    "rag_top_k",
                    "rag_min_similarity",
                    "rag_strategy",
                    "rag_reranker",
                    "reranker_min_score",
                )
            }
            report["configuration"]["langsmith_enabled"] = False
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
                            seed(session, corpus, settings)
                            app.dependency_overrides[get_settings] = dependency(settings)
                            app.dependency_overrides[get_session] = dependency(session)
                            app.dependency_overrides[get_provider] = dependency(
                                CompatibleProvider(settings)
                            )
                            with TestClient(
                                app,
                                headers={"X-DataPilot-CSRF": "1"},
                                raise_server_exceptions=False,
                            ) as client:
                                for case in cases:
                                    authorized = [
                                        d["chunk_id"]
                                        for d in corpus["documents"]
                                        if d["owner"] == case["owner"]
                                        and (d["personal"] or d["dataset"] == case["dataset"])
                                    ]
                                    start = time.perf_counter()
                                    try:
                                        found = retrieve_knowledge(
                                            session,
                                            corpus["owners"][case["owner"]],
                                            corpus["datasets"][case["dataset"]]["id"],
                                            case["query"],
                                            settings,
                                        )
                                    except AppError as error:
                                        found = {
                                            "status": "denied"
                                            if error.status_code == 404
                                            else "error",
                                            "citations": [],
                                            "error_code": error.detail["code"],
                                        }
                                    record = {
                                        "id": case["id"],
                                        "retrieval_status": found["status"],
                                        "retrieval_ms": round(
                                            (time.perf_counter() - start) * 1000, 3
                                        ),
                                        "authorized_chunk_ids": authorized,
                                        "retrieved_ids": [
                                            c["chunk_id"] for c in found["citations"]
                                        ],
                                        "citations": found["citations"],
                                    }
                                    if run_downstream and case["run_downstream"]:
                                        record["downstream"] = downstream(
                                            client, session, corpus, case, settings, found
                                        )
                                    report["records"].append(record)
                                    output.write_text(
                                        json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8",
                                    )
                finally:
                    transaction.rollback()
                    app.dependency_overrides.clear()
                report["database_rollback_verified"] = counts(connection) == before
            report["summary"] = summarize_rag(cases, report["records"], settings.rag_top_k)
    finally:
        engine.dispose()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            with cache.open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "corpus": hashes,
                        "configuration": report.get("configuration"),
                        "vectors": observation.vectors,
                    },
                    stream,
                    ensure_ascii=False,
                )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("EVAL_DATABASE_URL"))
    parser.add_argument("--split", choices=["development", "holdout"], default="development")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--embedding-cache", type=Path)
    parser.add_argument("--downstream", action="store_true")
    parser.add_argument("--config", type=Path, help="JSON retrieval configuration only")
    parser.add_argument("--freeze", type=Path, help="Frozen manifest required for holdout")
    args = parser.parse_args()
    if not args.live or not args.database_url:
        parser.error("Explicit --live and independent EVAL_DATABASE_URL required")
    updates = json.loads(args.config.read_bytes()) if args.config else {}
    allowed = {"rag_strategy", "rag_reranker", "rag_min_similarity", "reranker_min_score"}
    if set(updates) - allowed:
        parser.error("Only retrieval settings may be overridden")
    if args.split == "holdout":
        if not args.freeze:
            parser.error("Holdout requires a frozen manifest before the first run")
        frozen = json.loads(args.freeze.read_bytes())
        _, hashes = load_corpus()
        if frozen["corpus_sha256"] != hashes["sha256"] or updates not in (
            frozen["selected"],
            frozen["baseline"],
        ):
            parser.error("Frozen corpus/config mismatch")
        prompt_sha = hashlib.sha256(
            (Path(__file__).parent / "analysis_agent.py").read_bytes()
        ).hexdigest()
        if frozen["agent_source_sha256"] != prompt_sha:
            parser.error("Frozen Agent source mismatch")
        for name, sha in frozen.get("implementation_sha256", {}).items():
            if name not in {
                "knowledge.py",
                "embeddings.py",
                "analysis_agent.py",
                "analysis_graph.py",
                "providers.py",
                "retrieval.py",
                "reranking.py",
                "config.py",
            }:
                parser.error("Invalid frozen implementation path")
            if hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() != sha:
                parser.error("Frozen retrieval implementation mismatch")
    report = evaluate(
        args.database_url,
        args.split,
        args.output,
        run_downstream=args.downstream,
        cache=args.embedding_cache,
        updates=updates,
    )
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
