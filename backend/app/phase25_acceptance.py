"""Synthetic-only native Agent -> guarded SQL trial, separate from production defaults."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx
import sqlglot
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.analysis_agent import AGENT_SYSTEM, AnalysisPlan, PlannerContext, plan_analysis
from app.analysis_graph import AnalysisRun, execute_analysis_graph
from app.auth import COOKIE, token_hash
from app.config import ROOT, Settings, get_settings
from app.datasets import dataset_path, find_dataset
from app.db import get_session
from app.embeddings import CompatibleEmbeddingProvider
from app.errors import AppError
from app.evidence_snapshot import begin_snapshot, capture_sources
from app.execution import execute_query
from app.knowledge import retrieve_knowledge
from app.main import app
from app.metric_resolution import MetricRegistry
from app.models import AuthSession, KnowledgeChunk, KnowledgeDocument, User
from app.providers import SYSTEM_PROMPT, CompatibleProvider
from app.rag24_budget import BudgetStop
from app.rag24_evaluate import temporary_database, write_new
from app.rag24_policy import select_knowledge
from app.rag_evaluate import counts
from app.trial_budget import InputEstimator, MeteredTransport, TrialLedger

FIXTURES = ROOT / "backend/app/evals/phase25"
LIVE = ROOT / "data/runtime/phases25-29-live"
TOKENIZER = ROOT / "data/tooling/rag24/tokenizer.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus():
    path = FIXTURES / "development-v1.json"
    receipt = json.loads(path.with_suffix(".review.json").read_bytes())
    if receipt != {"status": "human-confirmed", "sha256": sha(path)}:
        raise BudgetStop("phase25_labels_not_reviewed")
    return json.loads(path.read_bytes())


def authorization():
    return {
        "run_id": "phases25-29-20261022-v1",
        "scope": "explicit-user-five-stage-trial",
        "start_head": "48ca7654b3bba04a35ab55ad8a54d4bfd0104546",
        "chat": [30, 20000],
        "embedding": [50, 15000],
        "judge": 0,
        "langsmith": 0,
        "historical_phase21_usage": "unknown-preserved",
        "old_entry_points": "unchanged",
    }


def ledger(*, create=False):
    if os.getenv("RUN_LIVE_PHASE25") != "1":
        raise BudgetStop("phase25_explicit_opt_in_required")
    return TrialLedger(LIVE / "ledger.sqlite", authorization(), create=create)


def engine_for_trial():
    public = Settings(_env_file=ROOT / ".env.example")
    url = make_url(public.database_url).set(host="127.0.0.1", port=15635)
    return create_engine(url, connect_args={"connect_timeout": 5})


class CachedProvider:
    source = "provider"

    def __init__(self, settings, ledger):
        original = CompatibleEmbeddingProvider(settings)
        self.space, self.model, self.ledger = original.space, original.model, ledger

    def embed(self, inputs):
        return self.ledger.cache(self.space, inputs)


def settings_for_trial(directory):
    original = Settings()
    if original.llm_provider != "deepseek" or not original.llm_api_key:
        raise BudgetStop("phase25_real_chat_missing")
    if (
        original.embedding_provider != "openai_compatible"
        or not original.embedding_api_key
        or original.embedding_model != "text-embedding-v4"
    ):
        raise BudgetStop("phase25_real_embedding_missing")
    return original.model_copy(
        update={
            "data_dir": Path(directory),
            "agent_native_tools": True,
            "agent_max_model_calls": 1,
            "sql_repair_enabled": False,
            "datapilot_langsmith_enabled": False,
            "langsmith_api_key": "",
            "rag_evidence_policy": "legacy",
            "rag_strategy": "vector",
            "rag_min_similarity": 0.70,
            "rag_reranker": "none",
            "knowledge_requests_per_window": 120,
        }
    )


def transport(ledger, estimator, settings, case, channel, observe=None):
    return MeteredTransport(
        ledger,
        estimator,
        stage=25,
        kind="chat",
        case=case,
        channel=channel,
        provider=settings.llm_provider,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        observe=observe,
    )


def budget_plan():
    """No network. Bound two native one-response plans and SQL with complete schemas."""
    value, estimator = corpus(), InputEstimator(TOKENIZER)
    inputs = sorted(
        {d["content"] for d in value["documents"]} | {c["query"] for c in value["cases"]}
    )
    embedding_estimate, embedding_reserve = estimator.local.estimate(inputs)
    requests = []
    for case in value["cases"][:2]:
        document = next(d for d in value["documents"] if d["id"] == case["expected_sources"][0])
        source = {
            "chunk_id": "00000000-0000-0000-0000-000000000001",
            "document_id": "00000000-0000-0000-0000-000000000002",
            "chunk_index": 0,
            "source_type": document["source_type"],
            "title": document["title"],
            "excerpt": document["content"],
            "similarity": 0.99,
        }
        plan = AnalysisPlan(metrics=["metric"], knowledge_chunk_ids=[source["chunk_id"]])
        # Use the actual LangChain schema conversion; never spend a probe request.
        model = ChatOpenAI(model="offline-no-network", api_key="offline-placeholder")
        structured = model.bind_tools([AnalysisPlan])
        payload = {
            "model": "offline-no-network",
            "messages": [
                {
                    "role": "system",
                    "content": AGENT_SYSTEM + "Final response budget: one AnalysisPlan.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"question": case["query"], "untrusted_knowledge": [source]},
                        ensure_ascii=False,
                    ),
                },
            ],
            "tools": structured.kwargs["tools"],
            "temperature": 0,
            "max_tokens": 1000,
        }
        a_estimate, a_reserve = estimator.estimate("chat", payload)
        metrics = [json.loads(document["content"])] if case["kind"] == "metric" else []
        sql_payload = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "table": "data",
                            "schema": [
                                {"name": "state", "type": "VARCHAR"},
                                {"name": "fee", "type": "BIGINT"},
                                {"name": "cost", "type": "BIGINT"},
                            ],
                            "question": case["query"],
                            "untrusted_business_context": {
                                "analysis_plan": plan.model_dump(),
                                "business_knowledge": [source],
                                "registered_metrics": metrics,
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        }
        s_estimate, s_reserve = estimator.estimate("chat", sql_payload)
        requests.append(
            {
                "case": case["id"],
                "agent_estimate": a_estimate,
                "agent_reserve": a_reserve + 256,
                "sql_estimate": s_estimate,
                "sql_reserve": s_reserve + 256,
            }
        )
    total = sum(r["agent_reserve"] + r["sql_reserve"] for r in requests)
    return {
        "status": "fits" if total <= 8000 and embedding_reserve <= 2500 else "budget_insufficient",
        "chat_planned_http": 4,
        "chat_preflight_reserved_bound": total,
        "chat_payloads": requests,
        "embedding_planned_http": (len(inputs) + 9) // 10,
        "embedding_unique_inputs": len(inputs),
        "embedding_estimate": embedding_estimate,
        "embedding_reserve": embedding_reserve,
        "corpus_sha256": sha(FIXTURES / "development-v1.json"),
        "tokenizer_sha256": sha(TOKENIZER),
        "hosted_chat_tokenizer": (
            "not_publicly_verified; full JSON count with 50%+128 reserve; stop on exceed"
        ),
        "no_model_probe_requests": True,
        "agent_call_limit": 1,
        "repairs": 0,
    }


def embed():
    plan = budget_plan()
    if plan["status"] != "fits":
        raise BudgetStop("phase25_whole_plan_does_not_fit")
    settings = settings_for_trial(LIVE)
    isolated = engine_for_trial()
    try:
        with isolated.connect() as connection:
            if connection.scalar(text("SELECT version_num FROM alembic_version")) != "0006":
                raise BudgetStop("phase25_isolated_database_not_migrated")
            if not connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ):
                raise BudgetStop("phase25_pgvector_missing")
    finally:
        isolated.dispose()
    values = corpus()
    inputs = sorted(
        {d["content"] for d in values["documents"]} | {c["query"] for c in values["cases"]}
    )
    fixed = LIVE / "budget-plan-v1.json"
    if fixed.exists() and not (LIVE / "ledger.sqlite").exists():
        raise BudgetStop("phase25_ledger_missing_cannot_reinitialize")
    if not fixed.exists():
        write_new(fixed, plan)
    elif json.loads(fixed.read_bytes()) != plan:
        raise BudgetStop("phase25_plan_changed")
    writer = ledger(create=not (LIVE / "ledger.sqlite").exists())
    original = CompatibleEmbeddingProvider(settings)
    estimator = InputEstimator(TOKENIZER)
    try:
        pending = []
        for item in inputs:
            try:
                writer.cache(original.space, [item])
            except BudgetStop as error:
                if str(error) != "trial_cache_miss_no_network_fallback":
                    raise
                pending.append(item)
        for start in range(0, len(pending), 10):
            metered = MeteredTransport(
                writer,
                estimator,
                stage=25,
                kind="embedding",
                case="p25-index",
                channel=f"batch-{start // 10}",
                provider=settings.embedding_provider,
                model=original.model,
                base_url=settings.embedding_base_url,
                space=original.space,
            )
            CompatibleEmbeddingProvider(settings, transport=metered).embed(
                pending[start : start + 10]
            )
        return writer.export()
    finally:
        writer.close()


def seed(session, settings, value):
    users, targets, documents = {}, {}, {}
    for key in ("reader", "other"):
        user = User(username="phase25-" + secrets.token_hex(8), password_hash="unused-synthetic")
        session.add(user)
        session.flush()
        token = secrets.token_urlsafe(32)
        session.add(
            AuthSession(
                token_hash=token_hash(token),
                user_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        users[key] = (user.id, token)
    session.commit()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(
        app, raise_server_exceptions=False, headers={"X-DataPilot-CSRF": "1"}
    ) as client:
        for name, owner in (("target", "reader"), ("sibling", "reader"), ("other", "other")):
            client.cookies.set(COOKIE, users[owner][1])
            response = client.post(
                "/api/datasets",
                files={"file": ("services.csv", (FIXTURES / "services.csv").read_bytes())},
            )
            if response.status_code != 201:
                raise BudgetStop("phase25_dataset_api_failed")
            targets[name] = response.json()["id"]
        for document in value["documents"]:
            client.cookies.set(COOKIE, users[document["owner"]][1])
            response = client.post(
                f"/api/datasets/{targets[document['dataset']]}/knowledge",
                json={k: document[k] for k in ("title", "source_type", "content")},
            )
            if response.status_code != 201 or response.json()["status"] != "ready":
                raise BudgetStop("phase25_knowledge_api_failed")
            documents[document["id"]] = response.json()["id"]
        client.cookies.set(COOKIE, users["reader"][1])
        denied = client.get(f"/api/datasets/{targets['other']}")
        if denied.status_code != 404:
            raise BudgetStop("phase25_foreign_dataset_not_denied")
    return users["reader"][0], targets, documents


def check_current(session, owner, dataset, snapshots, selected):
    for saved in snapshots:
        if saved["chunk_id"] not in selected:
            continue
        row = session.execute(
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument)
            .where(KnowledgeChunk.id == saved["chunk_id"])
            .execution_options(populate_existing=True)
            .with_for_update(of=KnowledgeDocument)
        ).one_or_none()
        if not row:
            raise AppError(403, "evidence_source_denied", "来源已失效。")
        chunk, document = row
        if (
            document.owner_id != owner
            or document.dataset_id != dataset
            or document.status != "ready"
            or document.version != saved["document_version"]
            or chunk.content[:800] != saved["excerpt"]
            or chunk.embedding_space != document.embedding_space
        ):
            raise AppError(403, "evidence_source_denied", "来源版本或作用域已变更。")


def one_case(session, settings, writer, case, owner, target, documents, observe):
    if case["kind"] == "foreign_dataset":
        return {
            "id": case["id"],
            "status": "denied",
            "api_http_status": 404,
            "evidence_kind": "real_API_permission_gate_no_model",
            "execute_calls": 0,
            "assertions_passed": True,
        }
    dataset = find_dataset(UUID(target), session, owner)
    registry = MetricRegistry(session, owner, target, settings)
    resolution = registry.resolve(case["query"], question=True)
    raw = retrieve_knowledge(session, owner, target, case["query"], settings)
    sources, evidence = select_knowledge(registry, case["query"], raw.get("citations", []))
    estimator = InputEstimator(TOKENIZER)
    events = []
    provider = CompatibleProvider(
        settings, transport=transport(writer, estimator, settings, case["id"], "sql", observe)
    )
    run = AnalysisRun(
        provider,
        settings,
        {},
        lambda sql: (
            events.append(sql) or execute_query(dataset_path(settings, target), sql, settings)
        ),
        case["query"],
        dataset.profile["fields"],
    )
    run.metric_resolution = registry.summary(resolution)
    if resolution["status"] == "resolved":
        run.registered_metrics = [i["contract"].model_dump() for i in resolution["items"]]

    def retrieve():
        rag = {**raw, "citations": sources, "evidence": evidence}
        begin_snapshot(
            rag, "phase25-experiment-C", resolution["status"], [s["chunk_id"] for s in sources]
        )
        rag["citations"] = capture_sources(session, owner, target, rag, sources)
        return rag

    run.retriever = retrieve

    def planning():
        if not sources:
            return {
                "needs_clarification": True,
                "clarification_questions": [registry.clarification(resolution)],
                "knowledge_chunk_ids": [],
            }
        context = PlannerContext(session, owner, target, settings, run.deadline)
        context.knowledge = run.rag["citations"].copy()
        # Actual ChatOpenAI + actual create_agent/ToolStrategy, with only a metered
        # HTTP client; no predefined tool calls/results in this real path.
        client = httpx.Client(
            transport=transport(writer, estimator, settings, case["id"], "agent", observe),
            follow_redirects=False,
        )
        model = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            max_retries=0,
            max_tokens=1000,
            temperature=0,
            timeout=settings.llm_timeout_seconds,
            http_client=client,
            extra_body={"thinking": {"type": "disabled"}},
        )
        try:
            plan = plan_analysis(context, case["query"], model=model)
        finally:
            client.close()
            run.agent_metrics = context.metadata()
        if not {s["chunk_id"] for s in sources} <= set(plan.knowledge_chunk_ids):
            plan.needs_clarification = True
            plan.clarification_questions = ["规划未采用适用来源，请确认口径。"]
        selected = set(plan.knowledge_chunk_ids)
        check_current(session, owner, target, run.rag["evidence"]["source_snapshots"], selected)
        run.rag["citations"] = [s for s in sources if s["chunk_id"] in selected]
        return plan.model_dump()

    run.planner = planning

    def evidence_guard(sql):
        check_current(
            session,
            owner,
            target,
            run.rag["evidence"]["source_snapshots"],
            set(run.plan.get("knowledge_chunk_ids", [])),
        )
        if resolution["status"] == "resolved":
            registry.check_sql(sql, resolution)

    run.evidence_guard = evidence_guard
    error = None
    try:
        execute_analysis_graph(run)
        status = "clarification" if run.plan.get("needs_clarification") else "success"
    except AppError as failure:
        status, error = "error", failure.detail["code"]
    actual_ids = [
        next(k for k, v in documents.items() if v == s["document_id"])
        for s in run.rag.get("citations", [])
    ]
    correct = status == case["expected_status"] and actual_ids == case["expected_sources"]
    if status == "success":
        columns = [column["name"] for column in run.result["columns"]]
        rows = [[row[name] for name in columns] for row in run.result["rows"]]
        correct = (
            correct and columns == case["columns"] and rows == case["rows"] and len(events) == 1
        )
    else:
        correct = correct and not events
    return {
        "id": case["id"],
        "question": case["query"],
        "status": status,
        "error_code": error,
        "evidence_kind": (
            "real_native_agent_sql"
            if settings.llm_provider != "mock" and run.agent_metrics.get("model_calls")
            else "offline_model_sql"
            if run.agent_metrics.get("model_calls")
            else "real_database_deterministic_evidence_gate_no_model"
        ),
        "workflow": run.metadata(),
        "sql": run.sql,
        "sql_ast": sqlglot.parse_one(run.sql, read="duckdb").dump()
        if run.sql and not error
        else None,
        "execute_calls": len(events),
        "result": run.result,
        "expected_result": {key: case[key] for key in ("columns", "rows") if key in case},
        "adopted_sources": actual_ids,
        "assertions_passed": bool(correct),
        "nodes": run.nodes,
    }


def evaluate(round_name, threshold=0.70):
    if not round_name.replace("-", "").isalnum() or len(round_name) > 60:
        raise BudgetStop("phase25_invalid_round")
    output = ROOT / "docs/phase25" / round_name
    output.mkdir(parents=True, exist_ok=False)
    value = corpus()
    writer = ledger()
    settings = settings_for_trial(LIVE)
    if threshold not in {0.65, 0.70}:
        raise BudgetStop("phase25_development_threshold_not_supported")
    settings = settings.model_copy(update={"rag_min_similarity": threshold})
    prior = sum(
        row["input_tokens"] or 0
        for row in writer.export()["requests"]
        if row["stage"] == 25 and row["kind"] == "chat"
    )
    if prior + budget_plan()["chat_preflight_reserved_bound"] > 8000:
        writer.close()
        raise BudgetStop("phase25_remaining_whole_round_budget_insufficient")
    cached = CachedProvider(settings, writer)
    cached.embed(
        sorted({d["content"] for d in value["documents"]} | {c["query"] for c in value["cases"]})
    )
    write_new(
        output / "manifest.json",
        {
            "start_head": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
            "corpus_sha256": sha(FIXTURES / "development-v1.json"),
            "source_sha256": {p.name: sha(p) for p in (ROOT / "backend/app").glob("*.py")},
            "config": {
                "candidate": "C-only-trial",
                "threshold": threshold,
                "agent_max_model_calls": 1,
                "repairs": 0,
                "cloud_tracing": False,
            },
            "before_ledger": writer.export(),
        },
    )
    events, records = [], []

    def observe(case, channel, payload, body):
        # Only synthetic acceptance messages/tool calls; never headers/endpoints.
        message = body.get("choices", [{}])[0].get("message", {})
        events.append(
            {
                "case": case,
                "channel": channel,
                "tool_calls": message.get("tool_calls", []),
                "provider_response_id": body.get("id"),
                "usage": body.get("usage"),
                "request_body_sha256": hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
            }
        )
        write_new(output / f"http-{len(events):02d}.json", events[-1])

    base = engine_for_trial()
    try:
        with base.connect() as c:
            before = counts(c)
        with tempfile.TemporaryDirectory(dir=LIVE, prefix="phase25-") as directory:
            settings = settings.model_copy(update={"data_dir": Path(directory)})
            with temporary_database(base) as (engine, cleanup):
                with (
                    Session(engine, expire_on_commit=False) as session,
                    patch("app.knowledge.get_embedding_provider", lambda _: cached),
                    patch("app.embeddings.get_embedding_provider", lambda _: cached),
                    tracing_context(enabled=False),
                ):
                    owner, targets, documents = seed(session, settings, value)
                    with engine.connect() as c:
                        persisted = counts(c)
                        assert (
                            persisted["knowledge_documents"] == 8
                            and persisted["knowledge_chunks"] == 8
                        )
                    for case in value["cases"]:
                        writer.healthy()
                        try:
                            record = one_case(
                                session,
                                settings,
                                writer,
                                case,
                                owner,
                                targets["target"],
                                documents,
                                observe,
                            )
                        except Exception as failure:
                            record = {
                                "id": case["id"],
                                "status": "stopped",
                                "error_type": type(failure).__name__,
                                "assertions_passed": False,
                            }
                        records.append(record)
                        write_new(output / (case["id"] + ".json"), record)
                        if record["status"] == "stopped" or writer.export()["stopped"]:
                            break
        with base.connect() as c:
            after = counts(c)
        result = {
            "status": "PASS"
            if len(records) == 8 and all(r["assertions_passed"] for r in records)
            else "PARTIAL",
            "records": records,
            "ledger": writer.export(),
            "actual_native_tool_calls": sum(bool(e["tool_calls"]) for e in events),
            "real_agent_sql_cases": sum(
                r.get("evidence_kind") == "real_native_agent_sql" for r in records
            ),
            "base_counts_unchanged": before == after,
            "cross_connection_persisted": persisted,
            "cleanup": cleanup,
            "ordinary_tool_execution": "NOT_RUN; native AnalysisPlan finalization only",
            "production_analysis_api": (
                "NOT_RUN; original graph/Agent/SQL composed in isolated runner"
            ),
        }
        write_new(output / "summary.json", result)
        return {k: v for k, v in result.items() if k not in {"records", "ledger"}}
    except Exception as failure:
        write_new(
            output / "failure.json",
            {
                "status": "stopped",
                "type": type(failure).__name__,
                "completed_records": len(records),
                "ledger": writer.export(),
            },
        )
        raise
    finally:
        writer.close()
        base.dispose()
        app.dependency_overrides.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "embed", "evaluate"))
    parser.add_argument("--round", default="development-01")
    parser.add_argument("--threshold", type=float, choices=(0.65, 0.70), default=0.70)
    args = parser.parse_args()
    try:
        result = (
            budget_plan()
            if args.action == "plan"
            else embed()
            if args.action == "embed"
            else evaluate(args.round, args.threshold)
        )
        print(json.dumps(result, ensure_ascii=True))
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


if __name__ == "__main__":
    main()
