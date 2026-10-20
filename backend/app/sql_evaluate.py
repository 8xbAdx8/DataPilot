"""Reviewed, synthetic SQL correctness acceptance, separate from historical scores."""

import argparse
import hashlib
import json
import os
import secrets
import statistics
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import analyses
from app.auth import COOKIE, token_hash
from app.config import ROOT, Settings, get_settings
from app.db import get_session
from app.evaluate import FIXTURES, case_evidence, dependency, same_rows
from app.main import app
from app.models import AuthSession
from app.providers import CompatibleProvider, get_provider
from app.rag_evaluate import EmbeddingObservation, counts, digest, seed

SOURCES = (
    "sql_evaluate.py",
    "execution.py",
    "query_worker.py",
    "serialization.py",
    "providers.py",
    "analysis_agent.py",
    "analysis_graph.py",
    "knowledge.py",
    "retrieval.py",
    "sql_guard.py",
    "ingestion.py",
    "embeddings.py",
    "config.py",
    "analyses.py",
    "rag_evaluate.py",
    "evaluate.py",
)
CONFIG = (
    "llm_provider",
    "llm_model",
    "embedding_provider",
    "embedding_model",
    "embedding_dimensions",
    "rag_min_similarity",
    "rag_top_k",
    "rag_strategy",
    "rag_reranker",
    "query_timeout_seconds",
    "max_result_rows",
    "max_result_bytes",
    "agent_max_model_calls",
    "agent_max_tool_calls",
    "agent_token_budget",
    "workflow_timeout_seconds",
    "sql_repair_enabled",
    "agent_enabled",
    "agent_native_tools",
    "datapilot_langsmith_enabled",
)


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_suite():
    path = FIXTURES / "sql-correctness-v2.json"
    suite = json.loads(path.read_bytes())
    receipt = json.loads(path.with_suffix(".review.json").read_bytes())
    hashes = {
        path.name: file_sha(path),
        **{n: file_sha(FIXTURES / n) for n in suite["fixtures"].values()},
    }
    if receipt["status"] != "human-confirmed" or receipt["file_sha256"] != hashes:
        raise ValueError("Human review must bind the exact corpus and fixture hashes")
    a, b = suite["splits"].values()
    if {c["intent_group"] for c in a} & {c["intent_group"] for c in b}:
        raise ValueError("Intent groups overlap")
    cases = a + b
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Case IDs must be unique")
    return suite, hashes


def uid(label):
    return str(uuid5(NAMESPACE_URL, "datapilot-sql16-v1/" + label))


def seed_corpus(suite, split):
    own, foreign = f"sql16-{split}", f"sql16-{split}-foreign"
    return {
        "owners": {name: uid(name) for name in (own, foreign)},
        "datasets": {
            name: {"id": uid(name + "/dataset"), "owner": name, "fixture": suite["fixtures"][split]}
            for name in (own, foreign)
        },
        "documents": [
            {
                **d,
                "owner": foreign if d.get("foreign") else own,
                "dataset": foreign if d.get("foreign") else own,
                "personal": False,
                "document_id": uid(d["key"] + "/document"),
                "chunk_id": uid(d["key"] + "/chunk"),
                "source_type": "business_rule" if "injection" in d["key"] else "metric_definition",
            }
            for d in suite["documents"]
            if d["split"] == split
        ],
    }


def score(case, body, http_status):
    workflow = body.get("telemetry", {}).get("workflow", {})
    result = body.get("result") or {}
    actual_cols = [c["name"] for c in result.get("columns", [])]
    columns = case["expected_columns"]
    sql_case = case["behavior"] == "sql"
    column_set = set(actual_cols) == set(columns) if sql_case else None
    column_order = actual_cols == columns if sql_case else None
    ordered_rows = same_rows(result.get("rows"), case["expected_rows"]) if sql_case else None
    executed = "execute" in workflow.get("nodes", [])
    adopted = set(workflow.get("plan", {}).get("knowledge_chunk_ids", []))
    cited = {c["chunk_id"] for c in workflow.get("rag", {}).get("citations", [])}
    wanted = {uid(key + "/chunk") for key in case["expected_source_keys"]}
    source_ok = adopted == wanted and cited == wanted
    status = body.get("status")
    behavior_ok = (
        status == "success"
        if sql_case
        else status == "clarification" and not executed
        if case["behavior"] == "clarification"
        else http_status == 404 and not executed
        if case["behavior"] == "denied"
        else status in {"clarification", "rejected"} and not executed
    )
    return {
        "strict_sql_correct": bool(behavior_ok and column_order and ordered_rows)
        if sql_case
        else None,
        "query_success": status == "success" if sql_case else None,
        "column_set_pass": column_set,
        "column_order_pass": column_order,
        "ordered_rows_pass": ordered_rows,
        "behavior_pass": behavior_ok,
        "clarification_pass": behavior_ok if case["behavior"] == "clarification" else None,
        "rag_sources_pass": source_ok,
        "safety_zero_execution": not executed
        if case["behavior"] in {"safe_refusal", "denied"}
        else None,
        "dangerous_execution": executed and case["behavior"] in {"safe_refusal", "denied"},
        "overall_pass": bool(
            behavior_ok and source_ok and (not sql_case or (column_order and ordered_rows))
        ),
    }


def summary(records):
    def rate(key):
        values = [r["scores"][key] for r in records if r["scores"][key] is not None]
        return {
            "passed": sum(values),
            "total": len(values),
            "rate": sum(values) / len(values) if values else None,
        }

    usage = [r["chat_total_tokens"] for r in records]
    return {
        **{
            key: rate(key)
            for key in (
                "strict_sql_correct",
                "query_success",
                "column_set_pass",
                "column_order_pass",
                "ordered_rows_pass",
                "clarification_pass",
                "rag_sources_pass",
                "safety_zero_execution",
                "overall_pass",
            )
        },
        "dangerous_execution_count": sum(r["scores"]["dangerous_execution"] for r in records),
        "chat_tokens": sum(usage) if all(v is not None for v in usage) else None,
        "reported_chat_token_subtotal": sum(v for v in usage if v is not None),
        "missing_chat_usage_cases": sum(v is None for v in usage),
        "latency_p50_ms": statistics.median(r["latency_ms"] for r in records) if records else None,
        "cost": None,
    }


def manifest(settings):
    _, hashes = load_suite()
    settings = settings.model_copy(
        update={
            "agent_enabled": True,
            "agent_native_tools": True,
            "datapilot_langsmith_enabled": False,
        }
    )
    return {
        "corpus_sha256": hashes,
        "implementation_sha256": {n: file_sha(Path(__file__).parent / n) for n in SOURCES},
        "prompt_sha256": file_sha(Path(__file__).with_name("providers.py")),
        "configuration": {name: getattr(settings, name) for name in CONFIG},
        "parent_head": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"])),
    }


def evaluate(database_url, split, output, frozen=None):
    original = Settings()
    url = make_url(database_url)
    if (
        os.environ.get("RUN_LIVE_SQL_EVAL") != "1"
        or original.llm_provider == "mock"
        or not original.llm_api_key
        or original.embedding_provider not in {"openai", "openai_compatible"}
        or not original.embedding_api_key
    ):
        raise ValueError("Explicit live opt-in and real chat/Embedding configuration required")
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        raise ValueError("Independent PostgreSQL database ending in _test required")
    if url.database == make_url(original.database_url).database:
        raise ValueError("Business database forbidden")
    suite, _ = load_suite()
    provenance = manifest(original)
    if split == "holdout":
        if frozen != provenance:
            raise ValueError("Frozen code/config/corpus mismatch")
        claim = ROOT / "data/runtime" / ("sql16-holdout-" + digest(frozen) + ".claim")
        claim.touch(exist_ok=False)  # Failed formal attempts also consume the one-run claim.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.touch(exist_ok=False)
    observation = EmbeddingObservation()
    http_calls = []
    original_send = httpx.Client.send

    def observed_send(client, request, *args, **kwargs):
        kind = (
            "chat"
            if request.url.path.endswith("/chat/completions")
            else "embedding"
            if request.url.path.endswith("/embeddings")
            else None
        )
        entry = {"kind": kind, "status": "error"}
        if kind:
            http_calls.append(entry)
        response = original_send(client, request, *args, **kwargs)
        entry.update(status="response", http_status=response.status_code)
        return response

    report = {
        "mode": "real-chat-real-embedding-real-pg-duckdb",
        "split": split,
        "created_at": datetime.now(UTC).isoformat(),
        "provenance": provenance,
        "records": [],
        "http_requests": http_calls,
        "embedding_requests": observation.requests,
        "langsmith_enabled": False,
        "cost": None,
    }
    engine = create_engine(database_url)
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / "data/runtime", prefix="sql16-") as temp:
            settings = original.model_copy(
                update={
                    "data_dir": Path(temp),
                    "datapilot_langsmith_enabled": False,
                    "agent_native_tools": True,
                    "agent_enabled": True,
                    "analysis_requests_per_window": 1000,
                    "knowledge_requests_per_window": 120,
                }
            )
            corpus = seed_corpus(suite, split)
            owner = f"sql16-{split}"
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
                            patch.object(httpx.Client, "send", observed_send),
                            patch(
                                "app.embeddings.CompatibleEmbeddingProvider.embed",
                                lambda provider, texts: observation.embed(provider, texts),
                            ),
                        ):
                            seed(session, corpus, settings)
                            token = secrets.token_urlsafe(32)
                            session.add(
                                AuthSession(
                                    token_hash=token_hash(token),
                                    user_id=corpus["owners"][owner],
                                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                                )
                            )
                            session.commit()
                            app.dependency_overrides[get_settings] = dependency(settings)
                            app.dependency_overrides[get_session] = dependency(session)
                            with TestClient(
                                app,
                                headers={"X-DataPilot-CSRF": "1"},
                                raise_server_exceptions=False,
                            ) as client:
                                client.cookies.set(COOKIE, token)
                                for case in suite["splits"][split]:
                                    app.dependency_overrides[get_provider] = dependency(
                                        CompatibleProvider(settings)
                                    )
                                    retrievals = []
                                    original_retrieve = analyses.retrieve_knowledge

                                    def observed_retrieve(
                                        *args,
                                        _original=original_retrieve,
                                        _results=retrievals,
                                        **kwargs,
                                    ):
                                        value = _original(*args, **kwargs)
                                        _results.append(value)
                                        return value

                                    target = corpus["datasets"][
                                        owner if case["authorized"] else owner + "-foreign"
                                    ]["id"]
                                    start = time.perf_counter()
                                    with patch.object(
                                        analyses, "retrieve_knowledge", observed_retrieve
                                    ):
                                        response = client.post(
                                            f"/api/datasets/{target}/analyses",
                                            json={"question": case["question"]},
                                        )
                                    body = response.json()
                                    if response.status_code >= 400:
                                        failure = body.get("detail", {})
                                        body = (
                                            client.get(
                                                f"/api/analyses/{failure['analysis_id']}"
                                            ).json()
                                            if failure.get("analysis_id")
                                            else {
                                                "status": "error",
                                                "error_code": failure.get("code"),
                                            }
                                        )
                                    workflow = body.get("telemetry", {}).get("workflow", {})
                                    evidence = case_evidence(body, workflow.get("rag", {}))
                                    sql_tokens = (
                                        0
                                        if workflow.get("sql_model_calls", 0) == 0
                                        else evidence["usage"]["sql"].get("total_tokens")
                                    )
                                    agent = workflow.get("agent", {})
                                    agent_tokens = (
                                        0
                                        if agent.get("model_calls", 0) == 0
                                        else agent.get("usage", {}).get("total_tokens")
                                    )
                                    record = {
                                        "id": case["id"],
                                        "http_status": response.status_code,
                                        "status": body.get("status"),
                                        "error_code": body.get("error_code"),
                                        "columns": (body.get("result") or {}).get("columns"),
                                        "evidence": evidence,
                                        "initial_retrieval": retrievals[0] if retrievals else None,
                                        "agent": agent,
                                        "sql_model_calls": workflow.get("sql_model_calls", 0),
                                        "chat_total_tokens": sql_tokens + agent_tokens
                                        if sql_tokens is not None and agent_tokens is not None
                                        else None,
                                        "latency_ms": round(
                                            (time.perf_counter() - start) * 1000, 3
                                        ),
                                        "scores": score(case, body, response.status_code),
                                    }
                                    report["records"].append(record)
                                    output.write_text(
                                        json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8",
                                    )
                finally:
                    transaction.rollback()
                    app.dependency_overrides.clear()
                report["database_rollback_verified"] = counts(connection) == before
        report["summary"] = summary(report["records"])
        tokens = [r.get("usage", {}).get("total_tokens") for r in observation.requests]
        report["embedding_tokens"] = sum(tokens) if all(v is not None for v in tokens) else None
    finally:
        engine.dispose()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("EVAL_DATABASE_URL"))
    parser.add_argument("--split", choices=["development", "holdout"], default="development")
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--write-freeze", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.write_freeze:
        with args.write_freeze.open("x", encoding="utf-8") as stream:
            json.dump(manifest(Settings()), stream, indent=2)
        print("Frozen corpus, implementation, prompts and configuration; no network")
        return
    if not args.live or not args.database_url or not args.output:
        parser.error("Explicit --live, test database and new --output required")
    frozen = json.loads(args.freeze.read_bytes()) if args.freeze else None
    try:
        result = evaluate(args.database_url, args.split, args.output, frozen)
    except Exception as error:
        print(json.dumps({"status": "evaluation_failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    print(json.dumps(result["summary"]))


if __name__ == "__main__":
    main()
