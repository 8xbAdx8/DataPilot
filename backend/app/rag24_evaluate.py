"""Explicit Stage24 Embedding-only trial. Fixed shared ledger, immutable artifacts."""

import argparse
import hashlib
import json
import math
import os
import secrets
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langsmith import tracing_context
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth import COOKIE, token_hash
from app.config import ROOT, Settings, get_settings
from app.db import get_session
from app.embeddings import CompatibleEmbeddingProvider
from app.errors import AppError
from app.knowledge import KnowledgeInput, retrieve_knowledge
from app.main import app
from app.metric_resolution import MetricRegistry, mentions
from app.models import AuthSession, KnowledgeChunk, KnowledgeDocument, User
from app.rag24_budget import BudgetStop, Ledger, TokenReservation, embed_batch
from app.rag_evaluate import counts
from app.rag_metrics import rank_scores
from app.retrieval_evidence import assess_evidence

FIXTURES = Path(__file__).parent / "evals/rag24"
LIVE_ROOT = ROOT / "data/runtime/rag24-live"
TOKENIZER = ROOT / "data/tooling/rag24/tokenizer.json"
AUTH_FILE = FIXTURES / "authorization-v1.json"
HOLDOUT_FREEZE = FIXTURES / "holdout-v1.freeze-v3.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def corpus(name):
    if name not in {"development-v1", "holdout-v1"}:
        raise BudgetStop("unknown_corpus")
    path = FIXTURES / (name + ".json")
    value = json.loads(path.read_bytes())
    receipt = json.loads(path.with_suffix(".review.json").read_bytes())
    if receipt["status"] != "human-confirmed" or receipt["sha256"] != sha(path):
        raise BudgetStop("review_not_bound_to_corpus")
    if len({d["id"] for d in value["documents"]}) != len(value["documents"]):
        raise BudgetStop("duplicate_document")
    if len({q["id"] for q in value["queries"]}) != len(value["queries"]):
        raise BudgetStop("duplicate_case")
    ids = {d["id"] for d in value["documents"]}
    for document in value["documents"]:
        KnowledgeInput.model_validate({k: document[k] for k in ("title", "source_type", "content")})
        if len(document["content"]) > 700:
            raise BudgetStop("single_chunk_synthetic_contract_exceeded")
    for query in value["queries"]:
        if not set(query["relevant_ids"]) <= ids or query["owner"] not in {"reader", "other"}:
            raise BudgetStop("invalid_label_reference")
    return value


def pool(value):
    return sorted(
        {d["content"] for d in value["documents"]} | {q["query"] for q in value["queries"]}
    )


def test_engine():
    # Never use the developer's business DB URL. This port belongs to the
    # separate datapilot-rag24 Compose project, with public demo credentials.
    settings = Settings(_env_file=ROOT / ".env.example")
    url = make_url(settings.database_url).set(host="127.0.0.1", port=15632, database="datapilot")
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        if connection.scalar(text("SELECT version_num FROM alembic_version")) != "0006":
            raise BudgetStop("isolated_database_not_migrated")
        if not connection.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        ):
            raise BudgetStop("pgvector_missing")
    return engine


@contextmanager
def temporary_database(base):
    """True API commits, independently visible; drop only this newly created DB."""
    name = "rag24_" + secrets.token_hex(8)
    if not name.startswith("rag24_") or not name.replace("_", "").isalnum():
        raise BudgetStop("invalid_temporary_database_name")
    created, engine = False, None
    state = {"database_name": name, "database_created": False, "database_removed": False}
    with base.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        try:
            admin.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = state["database_created"] = True
            url = base.url.set(database=name)
            environment = {
                **os.environ,
                "DATABASE_URL": url.render_as_string(hide_password=False),
                "LLM_PROVIDER": "mock",
                "EMBEDDING_PROVIDER": "none",
                "LLM_API_KEY": "",
                "EMBEDDING_API_KEY": "",
                "LANGSMITH_API_KEY": "",
                "DATAPILOT_LANGSMITH_ENABLED": "false",
            }
            migrated = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=60,
            )
            if migrated.returncode:
                raise BudgetStop("temporary_database_migration_failed")
            engine = create_engine(url, connect_args={"connect_timeout": 5})
            yield engine, state
        finally:
            if engine is not None:
                engine.dispose()
            if created:
                admin.exec_driver_sql(f'DROP DATABASE "{name}"')
                state["database_removed"] = True


def authorization(settings):
    approved = json.loads(AUTH_FILE.read_bytes())
    if approved["scope"] != "stage24-embedding-only" or approved["limits"] != {
        "requests": 80,
        "input_tokens": 20000,
        "chat": 0,
        "langsmith": 0,
    }:
        raise BudgetStop("authorization_scope_mismatch")
    old = ROOT / "docs/metric21-evidence-v1.json"
    if sha(old) != approved["prior_summary_sha256"]:
        raise BudgetStop("historical_evidence_changed")
    if (
        settings.embedding_model != "text-embedding-v4"
        or settings.embedding_dimensions != 1536
        or settings.embedding_provider != "openai_compatible"
        or not settings.embedding_api_key
        or settings.datapilot_langsmith_enabled
    ):
        raise BudgetStop("live_configuration_not_authorized")
    return {
        "approval_sha256": sha(AUTH_FILE),
        "space": CompatibleEmbeddingProvider(settings).space,
        "historical_usage": "unknown-preserved",
        "run_id": approved["run_id"],
    }


def live_ledger(settings, *, create=False):
    if os.getenv("RUN_LIVE_RAG24") != "1":
        raise BudgetStop("explicit_stage24_opt_in_required")
    return Ledger(LIVE_ROOT / "ledger.sqlite", authorization(settings), create=create)


def verify_freeze(freeze):
    for file, expected in {**freeze["source_sha256"], **freeze["asset_sha256"]}.items():
        path = (ROOT / file).resolve()
        if not path.is_relative_to(ROOT.resolve()):
            raise BudgetStop("holdout_frozen_file_changed")
        content = path.read_bytes()
        if freeze.get("hash_mode") == "canonical-lf":
            content = content.replace(b"\r\n", b"\n")
        if hashlib.sha256(content).hexdigest() != expected:
            raise BudgetStop("holdout_frozen_file_changed")


def embed_corpus(name):
    value, settings = corpus(name), Settings()
    engine = test_engine()  # Verify isolation/DB before spending anything.
    engine.dispose()
    estimator = TokenReservation(TOKENIZER)
    texts = pool(value)
    if name == "development-v1":
        estimate, reserve = estimator.estimate(texts)
        if reserve + 10000 > 20000 or math.ceil(len(texts) / 10) + 8 > 80:
            raise BudgetStop("whole_experiment_does_not_fit")
        plan = {
            "authorization_sha256": sha(AUTH_FILE),
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "source_sha256": {
                p: sha(Path(__file__).parent / p)
                for p in (
                    "rag24_budget.py",
                    "rag24_evaluate.py",
                    "embeddings.py",
                    "knowledge.py",
                    "retrieval.py",
                    "retrieval_evidence.py",
                    "metric_resolution.py",
                )
            },
            "corpus_sha256": sha(FIXTURES / (name + ".json")),
            "unique_texts": len(texts),
            "local_estimate": estimate,
            "conservative_reservation": reserve,
            "holdout_envelope": 10000,
            "tokenizer_sha256": sha(TOKENIZER),
            "max_batch": 10,
        }
        plan_path = ROOT / "docs/rag24/development-budget-plan-v1.json"
        if (LIVE_ROOT / "ledger.sqlite").exists():
            if json.loads(plan_path.read_bytes()) != plan:
                raise BudgetStop("existing_plan_changed")
            ledger = live_ledger(settings)
        else:
            write_new(plan_path, plan)
            ledger = live_ledger(settings, create=True)
    else:
        # The holdout must be frozen and reviewed before its first HTTP request.
        freeze = json.loads(HOLDOUT_FREEZE.read_bytes())
        verify_freeze(freeze)
        if freeze["corpus_sha256"] != sha(FIXTURES / "holdout-v1.json"):
            raise BudgetStop("holdout_freeze_changed")
        ledger = live_ledger(settings)
    try:
        missing = []
        ledger.healthy()
        for item in texts:
            try:
                ledger.cache([item])
            except BudgetStop as error:
                if str(error) != "cache_miss_no_network_fallback":
                    raise
                missing.append(item)
        # Entire pending pool must fit before its first request, not just batch1.
        if ledger.export()["known_input_tokens"] + estimator.estimate(missing)[1] > 20000:
            raise BudgetStop("pending_pool_over_budget")
        if ledger.export()["http_requests_reserved"] + math.ceil(len(missing) / 10) > 80:
            raise BudgetStop("pending_pool_over_request_limit")
        for index in range(0, len(missing), 10):
            embed_batch(
                ledger, settings, estimator, missing[index : index + 10], f"{name}-{index // 10}"
            )
        write_new(ROOT / f"docs/rag24/{name}-embedding-ledger.json", ledger.export())
        return ledger.export()
    except Exception:
        write_new(ROOT / f"docs/rag24/{name}-embedding-failed.json", ledger.export())
        raise
    finally:
        ledger.close()


class CachedRealProvider:
    source = "provider"

    def __init__(self, settings, ledger):
        original = CompatibleEmbeddingProvider(settings)
        self.space, self.model, self.ledger = original.space, original.model, ledger

    def embed(self, texts):
        return self.ledger.cache(texts)  # A miss fails; absolutely no HTTP fallback.


def summarize(value, records, strategy):
    rows = {r["id"]: r for r in records}
    result = {}
    for kind in ("metric", "knowledge", "negative", "safety"):
        cases = [q for q in value["queries"] if q["kind"] == kind]
        scores = [
            rank_scores(rows[q["id"]][strategy]["ids"], q["relevant_ids"], 4)
            for q in cases
            if q["relevant_ids"]
        ]
        result[kind] = {
            "cases": len(cases),
            **{
                k: statistics.mean(s[k] for s in scores) if scores else None
                for k in ("precision_at_k", "recall_at_k", "mrr", "ndcg_at_k")
            },
            "correct_no_match": sum(
                not rows[q["id"]][strategy]["ids"] for q in cases if not q["relevant_ids"]
            ),
            "irrelevant_source_count": sum(
                len(set(rows[q["id"]][strategy]["ids"]) - set(q["relevant_ids"])) for q in cases
            ),
        }
    result["reference_checks"] = sum(len(r[strategy]["authenticity"]) for r in records)
    result["reference_errors"] = sum(not ok for r in records for ok in r[strategy]["authenticity"])
    result["unauthorized_leaks"] = sum(
        len(set(r[strategy]["ids"]) - set(r["authorized_ids"])) for r in records
    )
    result["malicious_sources_returned"] = sum(
        i.startswith("inject_") for r in records for i in r[strategy]["ids"]
    )
    result["agent_selection"] = result["sql_execution"] = "NOT_RUN"
    result["wrong_agent_adoption"] = result["sql_formula_compliance"] = "NOT_RUN"
    latency = [r[strategy]["latency_ms"] for r in records if "latency_ms" in r[strategy]]
    result["retrieval_latency_p50_ms"] = statistics.median(latency) if latency else None
    result["retrieval_latency_p95_ms"] = (
        sorted(latency)[math.ceil(len(latency) * 0.95) - 1] if latency else None
    )
    documents = {d["id"]: d for d in value["documents"]}
    formal, aliases = [], []
    for query in value["queries"]:
        if query["kind"] != "metric":
            continue
        matched = bool(set(rows[query["id"]][strategy]["ids"]) & set(query["relevant_ids"]))
        doc = documents[query["relevant_ids"][0]]
        if mentions(query["query"], doc["title"]):
            formal.append(matched)
        elif any(
            mentions(query["query"], alias)
            for alias in json.loads(doc["content"]).get("aliases", [])
        ):
            aliases.append(matched)
    result["metric_capabilities"] = {
        "formal_names": {"cases": len(formal), "hits": sum(formal)},
        "registered_aliases": {"cases": len(aliases), "hits": sum(aliases)},
    }
    for expected in ("ambiguous", "inapplicable", "insufficient_evidence", "not_found"):
        cases = [q for q in value["queries"] if q["expected_evidence"] == expected]
        result["metric_capabilities"][expected] = {
            "cases": len(cases),
            "resolution_matches": sum(
                rows[q["id"]][strategy].get("metric_status") == expected for q in cases
            ),
            "returned_empty": sum(not rows[q["id"]][strategy]["ids"] for q in cases),
        }
    return result


def evaluate(name, round_id, configurations):
    if not re_round(round_id):
        raise BudgetStop("invalid_round_name")
    value, original = corpus(name), Settings()
    output = ROOT / "docs/rag24" / round_id
    output.mkdir(exist_ok=False)  # Immutable round; no accidental overwrite.
    ledger = live_ledger(original)
    provider = CachedRealProvider(original, ledger)
    ledger.cache(pool(value))  # All real vectors must exist before any DB work.
    start_manifest = {
        "split": name,
        "corpus_sha256": sha(FIXTURES / (name + ".json")),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_sha256": {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")},
        "configurations": configurations,
        "embedding_space": provider.space,
        "ledger_requests_before": ledger.export()["http_requests_reserved"],
    }
    if name == "holdout-v1":
        freeze = json.loads(HOLDOUT_FREEZE.read_bytes())
        verify_freeze(freeze)
        if (
            freeze["corpus_sha256"] != start_manifest["corpus_sha256"]
            or freeze["configurations"] != configurations
        ):
            raise BudgetStop("holdout_configuration_mismatch")
        write_new(
            LIVE_ROOT / "holdout-v1.claim.json",
            {"freeze_sha256": sha(HOLDOUT_FREEZE), "round": round_id},
        )
    write_new(output / "manifest.json", start_manifest)
    records = []
    base_engine = test_engine()
    try:
        with tempfile.TemporaryDirectory(prefix="rag24-", dir=ROOT / "data/runtime") as directory:
            settings = original.model_copy(
                update={
                    "data_dir": Path(directory),
                    "llm_provider": "mock",
                    "llm_api_key": "",
                    "langsmith_api_key": "",
                    "datapilot_langsmith_enabled": False,
                    "knowledge_requests_per_window": 120,
                    "rag_evidence_policy": "legacy",
                }
            )
            with base_engine.connect() as base_connection:
                base_before = counts(base_connection)
            with temporary_database(base_engine) as (engine, cleanup):
                with engine.connect() as connection:
                    before = counts(connection)
                with Session(engine, expire_on_commit=False) as session:
                    with (
                        patch("app.knowledge.get_embedding_provider", lambda _: provider),
                        patch("app.embeddings.get_embedding_provider", lambda _: provider),
                        tracing_context(enabled=False),
                    ):
                        records = run_database(session, settings, value, configurations, provider)
                    session.commit()
                with engine.connect() as second_connection:
                    persisted = counts(second_connection)
                    observed = second_connection.scalar(
                        select(func.count())
                        .select_from(KnowledgeDocument)
                        .where(KnowledgeDocument.status == "ready")
                    )
                    if observed != len(value["documents"]):
                        raise BudgetStop("cross_connection_persistence_failed")
            with base_engine.connect() as base_connection:
                after = counts(base_connection)
                if after != base_before or not cleanup["database_removed"]:
                    raise BudgetStop("test_database_cleanup_mismatch")
        summaries = {c["id"]: summarize(value, records, c["id"]) for c in configurations}
        write_new(output / "records.json", records)
        write_new(
            output / "summary.json",
            {
                "state": "complete",
                "strategies": summaries,
                "base_database_counts_unchanged": True,
                "database_counts_before": before,
                "database_counts_persisted": persisted,
                "base_database_counts_before": base_before,
                "base_database_counts_after": after,
                "cross_connection_persistence_verified": True,
                "cleanup": cleanup,
                "ledger": ledger.export(),
                "new_http_during_retrieval": ledger.export()["http_requests_reserved"]
                - start_manifest["ledger_requests_before"],
            },
        )
        return summaries
    except Exception as error:
        write_new(
            output / "failure.json",
            {"state": "error", "error_type": type(error).__name__, "completed_cases": len(records)},
        )
        raise
    finally:
        app.dependency_overrides.clear()
        base_engine.dispose()
        ledger.close()


def re_round(value):
    return value.isascii() and value.replace("-", "").isalnum() and len(value) <= 60


def run_database(session, settings, value, configurations, provider):
    owners, targets, docs = {}, {}, {}
    for owner in ("reader", "other"):
        user = User(username="rag24-" + secrets.token_hex(10), password_hash="unused-synthetic")
        session.add(user)
        session.flush()
        owners[owner] = user
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=owners["reader"].id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    session.commit()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(
        app, raise_server_exceptions=False, headers={"X-DataPilot-CSRF": "1"}
    ) as client:
        client.cookies.set(COOKIE, token)
        for target in ("target", "sibling"):
            response = client.post(
                "/api/datasets",
                files={
                    "file": (
                        "synthetic.csv",
                        (Path(__file__).parent / "evals" / value["data_fixture"]).read_bytes(),
                    )
                },
            )
            if response.status_code != 201:
                raise BudgetStop("synthetic_upload_failed")
            targets[target] = response.json()["id"]
        # The other user's knowledge is created under their own legitimate dataset.
        app.dependency_overrides[get_settings] = lambda: settings
        for source in value["documents"]:
            if source["owner"] == "other":
                other_token = secrets.token_urlsafe(32)
                session.add(
                    AuthSession(
                        token_hash=token_hash(other_token),
                        user_id=owners["other"].id,
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
                session.commit()
                client.cookies.set(COOKIE, other_token)
                response = client.post(
                    "/api/datasets", files={"file": ("other.csv", b"value\n1\n")}
                )
                target = response.json()["id"]
            else:
                client.cookies.set(COOKIE, token)
                target = targets.get(source["scope"], targets["target"])
            route = f"/api/datasets/{target}/knowledge"
            response = client.post(
                route, json={k: source[k] for k in ("title", "source_type", "content")}
            )
            if response.status_code != 201 or response.json()["status"] != "ready":
                raise BudgetStop("synthetic_knowledge_index_failed")
            docs[source["id"]] = response.json()["id"]
            if source["scope"] == "personal":
                # No personal-create REST route exists. Index through the actual
                # dataset API, then set only this synthetic fixture's scope.
                saved = session.get(KnowledgeDocument, docs[source["id"]])
                saved.dataset_id, saved.scope_key = None, "personal"
                session.commit()
        client.cookies.set(COOKIE, token)
        # One unmocked REST search proves API/cache/pgvector wiring as well.
        probe = client.post(
            f"/api/datasets/{targets['target']}/knowledge/search",
            json={"query": value["queries"][0]["query"]},
        )
        if probe.status_code != 200 or probe.json()["status"] == "error":
            raise BudgetStop("search_api_failed")
    mapping = {d: key for key, d in docs.items()}
    records = []
    for query in value["queries"]:
        authorized = [
            d["id"]
            for d in value["documents"]
            if d["owner"] == query["owner"] and d["scope"] in {query["dataset"], "personal"}
        ]
        if query["owner"] != "reader":
            authorized = []
        row = {
            "id": query["id"],
            "query": query["query"],
            "kind": query["kind"],
            "authorized_ids": authorized,
        }
        for config in configurations:
            current = settings.model_copy(
                update={
                    "rag_strategy": config["strategy"],
                    "rag_min_similarity": config["threshold"],
                    "rag_top_k": 4,
                    "rag_reranker": "none",
                }
            )
            start = time.perf_counter()
            try:
                result = retrieve_knowledge(
                    session,
                    owners[query["owner"]].id,
                    targets[query["dataset"]],
                    query["query"],
                    current,
                )
                if result["status"] not in {"retrieved", "no_match"}:
                    raise BudgetStop("retrieval_error_not_no_match")
                sources, evidence = result["citations"], {}
                registry = MetricRegistry(
                    session, owners[query["owner"]].id, targets[query["dataset"]], current
                )
                if config["policy"] == "registered":
                    sources, evidence = assess_evidence(registry, query["query"], sources)
                elif config["policy"] != "legacy":
                    from app.rag24_policy import select_knowledge

                    sources, evidence = select_knowledge(registry, query["query"], sources)
                authenticity = []
                for source in sources:
                    document, chunk = (
                        session.get(KnowledgeDocument, source["document_id"]),
                        session.get(KnowledgeChunk, source["chunk_id"]),
                    )
                    authenticity.append(
                        bool(
                            document
                            and chunk
                            and chunk.document_id == document.id
                            and document.owner_id == owners[query["owner"]].id
                            and document.dataset_id in {None, targets[query["dataset"]]}
                            and document.status == "ready"
                            and document.version == 1
                            and document.embedding_space == chunk.embedding_space == provider.space
                            and chunk.embedding_model == provider.model
                            and len(chunk.embedding) == 1536
                            and document.content
                            == next(
                                d["content"]
                                for d in value["documents"]
                                if d["id"] == mapping[document.id]
                            )
                            and source["excerpt"] == chunk.content[:800]
                            and source["title"] == document.title
                        )
                    )
                row[config["id"]] = {
                    "ids": [mapping[s["document_id"]] for s in sources],
                    "sources": sources,
                    "authenticity": authenticity,
                    "evidence": evidence,
                    "candidate_status": result["status"],
                    "candidate_stages": result.get("candidate_stages", []),
                    "metric_status": registry.resolve(query["query"], question=True)["status"],
                    "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                    "planned_source_ids": None,
                    "used_source_ids": None,
                }
            except AppError as error:
                if error.status_code != 404:
                    raise
                row[config["id"]] = {
                    "ids": [],
                    "authenticity": [],
                    "candidate_status": "denied",
                    "metric_status": "denied",
                }
        records.append(row)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("embed", "evaluate"))
    parser.add_argument("--corpus", choices=("development-v1", "holdout-v1"), required=True)
    parser.add_argument("--round", default="development-01")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "embed":
            result = embed_corpus(args.corpus)
            print(
                json.dumps(
                    {
                        k: result[k]
                        for k in (
                            "http_requests_reserved",
                            "known_input_tokens",
                            "usage_complete",
                            "stopped",
                        )
                    }
                )
            )
        else:
            if args.config is None:
                raise BudgetStop("configuration_file_required")
            result = evaluate(args.corpus, args.round, json.loads(args.config.read_bytes()))
            print(json.dumps(result, ensure_ascii=True))
    except Exception as error:
        print(
            json.dumps(
                {
                    "state": "stopped",
                    "error_type": type(error).__name__,
                    "reason": str(error)
                    if isinstance(error, BudgetStop)
                    else "external_or_storage_error_details_suppressed",
                }
            )
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
