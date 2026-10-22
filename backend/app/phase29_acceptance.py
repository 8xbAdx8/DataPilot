"""One frozen synthetic holdout, real cached embeddings and production analysis API.

No judge/tracing, no repair or retries. Durable shared HTTP ledger is authoritative.
"""

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

import httpx
import sqlglot
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.analysis_agent import plan_analysis
from app.auth import COOKIE, token_hash
from app.config import ROOT, Settings, get_settings
from app.db import get_session
from app.embeddings import CompatibleEmbeddingProvider
from app.errors import AppError
from app.execution import execute_query
from app.knowledge import retrieve_knowledge
from app.main import app
from app.metric_resolution import MetricRegistry
from app.models import Analysis, AuditEvent, AuthSession, KnowledgeChunk, KnowledgeDocument, User
from app.phase25_acceptance import LIVE, TOKENIZER, CachedProvider, authorization
from app.phase27_acceptance import score
from app.providers import CompatibleProvider, get_provider
from app.rag24_budget import BudgetStop
from app.rag24_evaluate import temporary_database, write_new
from app.rag24_policy import select_knowledge
from app.rag_evaluate import counts
from app.trial_budget import InputEstimator, MeteredTransport, TrialLedger

FIXTURES = ROOT / "backend/app/evals/phase29"
OUTPUT = ROOT / "docs/phase29/holdout-v1"
CONFIGS = [
    {"id": "A", "strategy": "vector", "threshold": 0.70},
    {"id": "C", "strategy": "vector", "threshold": 0.70},
]


class AdmissionTransport(MeteredTransport):
    """Propagate pre-reservation denials even when an application handler catches them."""

    def __init__(self, *args, control, **kwargs):
        super().__init__(*args, **kwargs)
        self.control = control

    def handle_request(self, request):
        try:
            return super().handle_request(request)
        except BudgetStop as error:
            self.control["denied"] = str(error)
            raise


def provider_dependency(provider):
    return lambda: provider


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus():
    path = FIXTURES / "holdout-v1.json"
    if json.loads(path.with_suffix(".review.json").read_bytes()) != {
        "status": "human-confirmed",
        "sha256": sha(path),
    }:
        raise BudgetStop("phase29_human_review_required")
    return json.loads(path.read_bytes())


def engine_for_trial():
    public = Settings(_env_file=ROOT / ".env.example")
    return create_engine(
        make_url(public.database_url).set(host="127.0.0.1", port=15639),
        connect_args={"connect_timeout": 5},
    )


def manifest():
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
        "files": {
            str(p.relative_to(ROOT)).replace("\\", "/"): sha(p)
            for p in sorted((ROOT / "backend/app").rglob("*.py"))
        },
        "assets": {
            str(p.relative_to(ROOT)).replace("\\", "/"): sha(p)
            for p in sorted(FIXTURES.glob("*"))
            if p.is_file()
        },
        "configurations": CONFIGS,
        "agent": {
            "max_model_calls": 1,
            "max_tools": "unchanged production4",
            "sql_repair": False,
            "experiment_mode": "enabled",
            "D": "NOT_APPLICABLE",
        },
        "scoring": "phase27_acceptance.score fixed@4; all relevant required for complete evidence",
        "api_cases": corpus()["e2e_case_ids"],
        "stop": "unknown usage, reservation denial or uncertainty ends all external requests",
    }


def freeze():
    corpus()
    write_new(ROOT / "docs/phase29/freeze-v1.json", manifest())
    return {"status": "frozen", "sha256": sha(ROOT / "docs/phase29/freeze-v1.json")}


def seed(engine, settings, value, client):
    actors, targets, documents = {}, {}, {}
    with Session(engine) as session:
        for key in ("reader", "other"):
            user = User(
                username="studio29-" + secrets.token_hex(8), password_hash="synthetic-unused"
            )
            session.add(user)
            session.flush()
            token = secrets.token_urlsafe(32)
            session.add(
                AuthSession(
                    user_id=user.id,
                    token_hash=token_hash(token),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            actors[key] = (user.id, token)
        session.commit()
    for key, owner in (("target", "reader"), ("sibling", "reader"), ("other", "other")):
        client.cookies.set(COOKIE, actors[owner][1])
        response = client.post(
            "/api/datasets",
            files={"file": ("bookings.csv", (FIXTURES / "bookings.csv").read_bytes())},
        )
        if response.status_code != 201:
            raise BudgetStop("phase29_dataset_seed_failed")
        targets[key] = response.json()["id"]
    for doc in value["documents"]:
        client.cookies.set(COOKIE, actors[doc["owner"]][1])
        response = client.post(
            f"/api/datasets/{targets[doc['dataset']]}/knowledge",
            json={k: doc[k] for k in ("title", "source_type", "content")},
        )
        if response.status_code != 201 or response.json()["status"] != "ready":
            raise BudgetStop("phase29_knowledge_seed_failed")
        documents[response.json()["id"]] = doc["id"]
    client.cookies.set(COOKIE, actors["reader"][1])
    return actors["reader"][0], targets, documents


def verify_sources(engine, sources, owner, target, mapping, value, provider):
    checks = []
    with Session(engine) as session:
        for source in sources:
            doc = session.get(KnowledgeDocument, source["document_id"])
            chunk = session.get(KnowledgeChunk, source["chunk_id"])
            fixture = next(d for d in value["documents"] if d["id"] == mapping[doc.id])
            valid = bool(
                doc.owner_id == owner
                and doc.dataset_id in {None, target}
                and doc.status == "ready"
                and doc.version == 1
                and doc.content == chunk.content == fixture["content"]
                and chunk.document_id == doc.id
                and chunk.content[:800] == source["excerpt"]
                and len(chunk.embedding) == 1536
                and doc.embedding_space == chunk.embedding_space == provider.space
                and chunk.embedding_model == provider.model
            )
            checks.append(
                {
                    "chunk_id": chunk.id,
                    "document_id": doc.id,
                    "document_version": doc.version,
                    "valid": valid,
                }
            )
    if not all(c["valid"] for c in checks):
        raise BudgetStop("phase29_reference_or_permission_violation")
    return checks


def run():
    if os.getenv("RUN_LIVE_PHASE29") != "1":
        raise BudgetStop("phase29_explicit_opt_in_required")
    if json.loads((ROOT / "docs/phase29/freeze-v1.json").read_bytes()) != manifest():
        raise BudgetStop("phase29_freeze_mismatch")
    OUTPUT.mkdir(parents=True, exist_ok=False)  # Never score this holdout twice.
    value, estimator = corpus(), InputEstimator(TOKENIZER)
    original = Settings()
    if (
        not original.embedding_api_key
        or not original.llm_api_key
        or original.embedding_model != "text-embedding-v4"
        or original.embedding_dimensions != 1536
        or original.llm_provider != "deepseek"
    ):
        raise BudgetStop("phase29_real_credentials_missing")
    settings = original.model_copy(
        update={
            "rag_experiment_mode": "off",
            "rag_experiment_synthetic": True,
            "rag_evidence_policy": "legacy",
            "rag_strategy": "vector",
            "rag_min_similarity": 0.70,
            "rag_reranker": "none",
            "rag_top_k": 4,
            "agent_max_model_calls": 1,
            "sql_repair_enabled": False,
            "knowledge_requests_per_window": 120,
            "datapilot_langsmith_enabled": False,
            "langsmith_api_key": "",
        }
    )
    ledger = TrialLedger(LIVE / "ledger.sqlite", authorization())
    ledger.healthy()
    provider = CachedProvider(settings, ledger)
    texts = sorted(
        {d["content"] for d in value["documents"]}
        | {q["query"] for q in value["queries"]}
        | {q["e2e_query"] for q in value["queries"] if "e2e_query" in q}
    )
    pending = []
    for text in texts:
        try:
            ledger.cache(provider.space, [text])
        except BudgetStop as error:
            if str(error) != "trial_cache_miss_no_network_fallback":
                raise
            pending.append(text)
    batches = [pending[i : i + 10] for i in range(0, len(pending), 10)]
    requests = [r for r in ledger.export()["requests"] if r["stage"] == 29]
    if len(batches) + sum(r["kind"] == "embedding" for r in requests) > 14:
        raise BudgetStop("phase29_request_forecast_exceeds_cap")
    write_new(
        OUTPUT / "budget-plan.json",
        {
            "embedding_pending_batches": len(batches),
            "unique_inputs": len(texts),
            "local_estimate": estimator.local.estimate(pending)[0],
            "sum_conservative_envelopes": sum(estimator.local.estimate(b)[1] for b in batches),
            "admission": "per HTTP actual-so-far plus full pending-request byte envelope <= cap",
            "embedding_cap": [14, 4000],
            "chat_cap": [18, 12000],
            "chat_cases": value["e2e_case_ids"],
            "forecast": (
                "at most3 real planning+SQL cases; others deterministic evidence/permission gates"
            ),
            "per_chat_request": "complete serialized messages/tools tokenizer +50%+128 before send",
            "completion_not_guaranteed": True,
            "budget_before": ledger.export(),
            "judge": 0,
            "langsmith": 0,
        },
    )
    base, retrieval_rows, api_rows = engine_for_trial(), [], []
    try:
        for i, batch in enumerate(batches):
            transport = MeteredTransport(
                ledger,
                estimator,
                stage=29,
                kind="embedding",
                case="studio-holdout",
                channel=f"batch-{i}",
                provider=settings.embedding_provider,
                model=provider.model,
                base_url=settings.embedding_base_url,
                space=provider.space,
            )
            CompatibleEmbeddingProvider(settings, transport=transport).embed(batch)
        with base.connect() as connection:
            before = counts(connection)
        with temporary_database(base) as (engine, cleanup):
            with tempfile.TemporaryDirectory(dir=LIVE, prefix="phase29-") as directory:
                settings = settings.model_copy(update={"data_dir": Path(directory)})

                def sessions():
                    with Session(engine, expire_on_commit=False) as session:
                        yield session

                app.dependency_overrides[get_session] = sessions
                app.dependency_overrides[get_settings] = lambda: settings
                with (
                    patch("app.knowledge.get_embedding_provider", lambda _: provider),
                    patch("app.embeddings.get_embedding_provider", lambda _: provider),
                    tracing_context(enabled=False),
                    TestClient(
                        app, raise_server_exceptions=False, headers={"X-DataPilot-CSRF": "1"}
                    ) as client,
                ):
                    owner, targets, mapping = seed(engine, settings, value, client)
                    for q in value["queries"]:
                        row = {k: q[k] for k in ("id", "query", "kind", "relevant_ids")}
                        for config in CONFIGS:
                            start = time.perf_counter()
                            with Session(engine) as session:
                                try:
                                    rag = retrieve_knowledge(
                                        session, owner, targets[q["dataset"]], q["query"], settings
                                    )
                                    if rag["status"] not in {"retrieved", "no_match"}:
                                        raise BudgetStop("phase29_retrieval_not_success")
                                    sources, decision = rag["citations"], {}
                                    raw = list(sources)
                                    if config["id"] == "C":
                                        registry = MetricRegistry(
                                            session, owner, targets[q["dataset"]], settings
                                        )
                                        sources, decision = select_knowledge(
                                            registry, q["query"], sources
                                        )
                                    valid = verify_sources(
                                        engine,
                                        raw + sources,
                                        owner,
                                        targets[q["dataset"]],
                                        mapping,
                                        value,
                                        provider,
                                    )
                                    row[config["id"]] = {
                                        "ids": [mapping[s["document_id"]] for s in sources],
                                        "sources": sources,
                                        "raw_candidates": raw,
                                        "decision": decision,
                                        "references": valid,
                                        "stages": rag.get("candidate_stages", []),
                                        "latency_ms": round(
                                            1000 * (time.perf_counter() - start), 3
                                        ),
                                        "planned_source_ids": None,
                                        "used_source_ids": None,
                                    }
                                except AppError as failure:
                                    if failure.status_code != 404:
                                        raise
                                    row[config["id"]] = {
                                        "ids": [],
                                        "sources": [],
                                        "http_status": 404,
                                        "permission_denied": True,
                                    }
                        retrieval_rows.append(row)
                        write_new(OUTPUT / (q["id"] + ".json"), row)
                    # Save the one official ranking before any Agent call.
                    write_new(OUTPUT / "retrieval-scores.json", score(retrieval_rows, CONFIGS))
                    settings = settings.model_copy(update={"rag_experiment_mode": "enabled"})
                    for q in value["queries"]:
                        if q["id"] not in value["e2e_case_ids"]:
                            continue
                        calls, executions, control = [], [], {}

                        def observe(case, channel, _payload, body, calls=calls):
                            calls.append(
                                {
                                    "case": case,
                                    "channel": channel,
                                    "model": body.get("model"),
                                    "usage": body.get("usage"),
                                    "id": body.get("id"),
                                    "choices": body.get("choices"),
                                }
                            )

                        def chat_transport(channel, case_id=q["id"], control=control):
                            return AdmissionTransport(
                                ledger,
                                estimator,
                                stage=29,
                                kind="chat",
                                case=case_id,
                                channel=channel,
                                provider=settings.llm_provider,
                                model=settings.llm_model,
                                base_url=settings.llm_base_url,
                                observe=observe,
                                control=control,
                            )

                        def planning(context, question):
                            with httpx.Client(
                                transport=chat_transport("agent"), follow_redirects=False
                            ) as http:
                                model = ChatOpenAI(
                                    model=settings.llm_model,
                                    api_key=settings.llm_api_key,
                                    base_url=settings.llm_base_url,
                                    max_retries=0,
                                    max_tokens=1000,
                                    temperature=0,
                                    timeout=settings.llm_timeout_seconds,
                                    http_client=http,
                                    extra_body={"thinking": {"type": "disabled"}},
                                )
                                return plan_analysis(context, question, model=model)

                        def execute(path, sql, configured, executions=executions):
                            result = execute_query(path, sql, configured)
                            executions.append(
                                {
                                    "sql": sql,
                                    "ast": sqlglot.parse_one(sql, read="duckdb").dump(),
                                    "result": result,
                                }
                            )
                            return result

                        sql_provider = CompatibleProvider(settings, transport=chat_transport("sql"))
                        app.dependency_overrides[get_provider] = provider_dependency(sql_provider)
                        started = time.perf_counter()
                        with (
                            patch("app.analyses.plan_analysis", planning),
                            patch("app.analyses.execute_query", execute),
                        ):
                            response = client.post(
                                f"/api/datasets/{targets[q['dataset']]}/analyses",
                                json={"question": q.get("e2e_query", q["query"])},
                            )
                        body = response.json()
                        # Any failed reservation caught by the API must still stop the trial.
                        ledger.healthy()
                        if control.get("denied"):
                            raise BudgetStop("phase29_chat_admission_denied_stop")
                        success_expected = q["id"] in {"h29-01", "h29-02", "h29-17"}
                        correct = (
                            body.get("status") == "success"
                            if success_expected
                            else (
                                (
                                    response.status_code == 404
                                    or body.get("status") == "clarification"
                                )
                                and not executions
                            )
                        )
                        if success_expected and correct:
                            columns = q.get("columns", value["e2e_fact_columns"])
                            expected = q.get("rows", value["e2e_fact_rows"])
                            result = body["result"]
                            correct = (
                                [c["name"] for c in result["columns"]] == columns
                                and [[r[c] for c in columns] for r in result["rows"]] == expected
                                and len(executions) == 1
                                and any(c["channel"] == "agent" for c in calls)
                            )
                            evidence = body["telemetry"]["workflow"]["rag"]["evidence"]
                            used = set(evidence["used_source_ids"])
                            used_documents = {
                                mapping[s["document_id"]]
                                for s in evidence["source_snapshots"]
                                if s["chunk_id"] in used
                            }
                            correct = correct and used_documents == set(q["relevant_ids"])
                        history, audits = None, []
                        if "id" in body:
                            history = client.get(f"/api/analyses/{body['id']}").json()
                            with Session(engine) as independent:
                                assert independent.get(Analysis, body["id"]) is not None
                                audits = [
                                    a.event
                                    for a in independent.scalars(
                                        select(AuditEvent).where(
                                            AuditEvent.analysis_id == body["id"]
                                        )
                                    ).all()
                                ]
                        record = {
                            "id": q["id"],
                            "question": q.get("e2e_query", q["query"]),
                            "http_status": response.status_code,
                            "response": body,
                            "history": history,
                            "audit_events": audits,
                            "model_http": calls,
                            "executions": executions,
                            "correct": correct,
                            "success_expected": success_expected,
                            "latency_ms": round(1000 * (time.perf_counter() - started), 3),
                        }
                        api_rows.append(record)
                        write_new(OUTPUT / (q["id"] + "-api.json"), record)
        with base.connect() as connection:
            after = counts(connection)
        assert before == after and cleanup["database_removed"]
        scores = score(retrieval_rows, CONFIGS)
        c = scores["C"]["by_kind"]
        positive_knowledge = [
            r for r in scores["C"]["per_case"] if r["kind"] in {"prose", "structured"}
        ]
        knowledge_recall = sum(r["recall_at_4"] for r in positive_knowledge) / 15
        metric_pass = c["metric"]["recall_at_4"] >= 0.9
        knowledge_pass = knowledge_recall >= 14 / 15
        refusal_pass = c["no_answer"]["correct_refusal"] >= 9
        safety_pass = c["safety"]["correct_refusal"] == 10
        all_api = all(r["correct"] for r in api_rows)
        result = {
            "status": "PASS"
            if all((metric_pass, knowledge_pass, refusal_pass, safety_pass, all_api))
            else "PARTIAL",
            "scores": scores,
            "knowledge_recall_at_4": knowledge_recall,
            "gates": {
                "metrics": metric_pass,
                "knowledge": knowledge_pass,
                "refusal": refusal_pass,
                "safety_retrieval": safety_pass,
                "api": all_api,
            },
            "api_n": len(api_rows),
            "api_correct": sum(r["correct"] for r in api_rows),
            "real_agent_success_n": sum(r["correct"] and r["success_expected"] for r in api_rows),
            "reference_permission_errors": 0,
            "cleanup": cleanup,
            "base_counts_unchanged": before == after,
            "ledger": ledger.export(),
            "D": "NOT_APPLICABLE; not silently enabled",
            "production_switch": "NOT_AUTHORIZED",
            "frontend": (
                "separate Mock/Fake Playwright engineering evidence; not live-model browser"
            ),
        }
        write_new(OUTPUT / "summary.json", result)
        return {k: result[k] for k in ("status", "gates", "api_n", "api_correct", "cleanup")}
    except Exception as failure:
        write_new(
            OUTPUT / "stopped.json",
            {
                "status": "BLOCKED",
                "type": type(failure).__name__,
                "reason": str(failure) if isinstance(failure, BudgetStop) else "details_suppressed",
                "retrieval_completed": len(retrieval_rows),
                "api_completed": len(api_rows),
                "ledger": ledger.export(),
            },
        )
        raise
    finally:
        base.dispose()
        ledger.close()
        app.dependency_overrides.clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "run"])
    try:
        print(json.dumps(freeze() if parser.parse_args().action == "freeze" else run()))
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
