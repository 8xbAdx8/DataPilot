"""Explicit synthetic development acceptance; raw tool bodies stay in this local harness."""

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
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.analysis_agent import PlannerContext
from app.auth import COOKIE, token_hash
from app.config import ROOT, Settings, get_settings
from app.db import get_session
from app.embeddings import CompatibleEmbeddingProvider
from app.evaluate import FIXTURES, dependency
from app.knowledge import metric_definition
from app.main import app
from app.models import AuthSession
from app.providers import CompatibleProvider, get_provider
from app.rag18_evaluate import write
from app.rag_evaluate import EmbeddingObservation, counts, seed


def check_prior_usage(directory):
    for name in (
        "metric21-baseline-v1.json",
        "metric21-development-live-v1.json",
        "metric21-legacy-live-v1.json",
    ):
        path = directory / name
        if path.exists():
            prior = json.loads(path.read_bytes())
            if prior.get("unknown_usage") or prior.get("state") != "complete":
                raise ValueError("Prior uncertain usage blocks additional live calls")


def run(mode, output):
    if os.environ.get("RUN_LIVE_METRIC21") != "1":
        raise ValueError("Explicit live development opt-in required")
    check_prior_usage(ROOT / "docs")
    original = Settings()
    if original.llm_provider == "mock" or original.embedding_provider in {"none", "fake"}:
        raise ValueError("Real providers required")
    if not original.llm_api_key or not original.embedding_api_key:
        raise ValueError("Missing separate real credentials")
    fixture = FIXTURES / "metric21-development-v1.json"
    corpus = json.loads(fixture.read_bytes())
    owner, target, document, chunk = (str(uuid4()) for _ in range(4))
    label = "metric21-" + owner[:8]
    subset = {
        "owners": {label: owner},
        "datasets": {"services": {"id": target, "fixture": corpus["data_fixture"], "owner": label}},
        "documents": [
            {
                "owner": label,
                "dataset": "services",
                "personal": False,
                "title": corpus["metric"]["name"],
                "source_type": "metric_definition",
                "content": json.dumps(corpus["metric"], ensure_ascii=False),
                "document_id": document,
                "chunk_id": chunk,
            }
        ],
    }
    if mode == "legacy-development":
        subset["documents"][0]["content"] = (
            corpus["metric"]["name"]
            + "：只统计 state='closed' 的服务，按 SUM(fee)-SUM(labor) 计算。"
        )
    observation, captures = EmbeddingObservation(), []
    original_record = PlannerContext.record_tool

    def observe(context, name, arguments, response):
        if context.dataset_id != target or context.owner_id != owner:
            raise ValueError("Raw diagnostics restricted to this synthetic dataset")
        captures.append(
            {"name": name, "arguments": arguments, "response": json.loads(response.content)}
        )
        original_record(context, name, arguments, response)

    prior = 0
    if mode in {"development", "legacy-development"}:
        previous = (
            "metric21-baseline-v1.json"
            if mode == "development"
            else "metric21-development-live-v1.json"
        )
        baseline = json.loads((ROOT / "docs" / previous).read_bytes())
        if baseline.get("unknown_usage"):
            raise ValueError("Unknown previous usage blocks further paid calls")
        prior = baseline["total_chat_tokens"]
    report = {
        "state": "claimed",
        "mode": mode,
        "records": [],
        "prior_chat_tokens": prior,
        "total_chat_tokens": prior,
        "unknown_usage": False,
        "token_stopline": 90000,
        "case_conservative_reservation": 70000,
        "dataset_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "sources_sha256": {
            name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
            for name in (
                "analysis_agent.py",
                "knowledge.py",
                "analyses.py",
                "analysis_graph.py",
                "metric_resolution.py",
            )
            if (Path(__file__).parent / name).exists()
        },
        "model": original.llm_model,
        "embedding_model": original.embedding_model,
        "cost": "unknown",
    }
    write(output, report, exclusive=True)
    url = make_url(original.database_url).set(host="127.0.0.1", database="datapilot_test")
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / "data/runtime", prefix="metric21-") as temp:
            settings = original.model_copy(
                update={
                    "data_dir": Path(temp),
                    "datapilot_langsmith_enabled": False,
                    "agent_native_tools": True,
                    "knowledge_requests_per_window": 100,
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
                            report["direct_lookup"] = {
                                name: metric_definition(session, owner, target, name, settings)
                                for name in (corpus["metric"]["name"], "服务实收")
                            }
                            token = secrets.token_urlsafe(32)
                            session.add(
                                AuthSession(
                                    token_hash=token_hash(token),
                                    user_id=owner,
                                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                                )
                            )
                            session.commit()
                            app.dependency_overrides[get_settings] = dependency(settings)
                            app.dependency_overrides[get_session] = dependency(session)
                            app.dependency_overrides[get_provider] = lambda: CompatibleProvider(
                                settings
                            )
                            with patch.object(PlannerContext, "record_tool", observe):
                                with TestClient(
                                    app,
                                    headers={"X-DataPilot-CSRF": "1"},
                                    raise_server_exceptions=False,
                                ) as client:
                                    client.cookies.set(COOKIE, token)
                                    cases = (
                                        [c for c in corpus["cases"] if c["id"] == "alias"]
                                        if mode == "baseline"
                                        else corpus["cases"]
                                    )
                                    if mode == "legacy-development":
                                        cases = [{**corpus["cases"][0], "id": "legacy-official"}]
                                    for case in cases:
                                        if (
                                            report["unknown_usage"]
                                            or report["total_chat_tokens"] + 70000 > 90000
                                        ):
                                            report["records"].append(
                                                {
                                                    "id": case["id"],
                                                    "blocked": "budget_or_unknown_usage",
                                                }
                                            )
                                            break
                                        captures.clear()
                                        start = time.perf_counter()
                                        response = client.post(
                                            f"/api/datasets/{target}/analyses",
                                            json={"question": case["question"]},
                                        )
                                        body = response.json()
                                        if response.status_code >= 400 and body.get(
                                            "detail", {}
                                        ).get("analysis_id"):
                                            body = client.get(
                                                "/api/analyses/" + body["detail"]["analysis_id"]
                                            ).json()
                                        workflow = body.get("telemetry", {}).get("workflow", {})
                                        sql_calls = workflow.get("sql_model_calls", 0)
                                        agent_calls = workflow.get("agent", {}).get(
                                            "model_calls", 0
                                        )
                                        sql_usage = body.get("telemetry", {}).get("usage", {})
                                        agent_usage = workflow.get("agent", {}).get("usage", {})
                                        sql_tokens = (
                                            sql_usage.get("total_tokens") if sql_calls else 0
                                        )
                                        agent_tokens = (
                                            agent_usage.get("total_tokens") if agent_calls else 0
                                        )
                                        if (
                                            type(sql_tokens) is not int
                                            or type(agent_tokens) is not int
                                        ):
                                            report["unknown_usage"] = True
                                        else:
                                            report["total_chat_tokens"] += sql_tokens + agent_tokens
                                        passed = (
                                            body.get("status") == "success"
                                            and body.get("result", {}).get("rows") == case["rows"]
                                            if "rows" in case
                                            else body.get("status") == "clarification"
                                            and "execute" not in workflow.get("nodes", [])
                                        )
                                        report["records"].append(
                                            {
                                                "id": case["id"],
                                                "question": case["question"],
                                                "http_status": response.status_code,
                                                "status": body.get("status"),
                                                "sql": body.get("sql"),
                                                "rows": (body.get("result") or {}).get("rows"),
                                                "error_code": body.get("error_code"),
                                                "summary": body.get("summary"),
                                                "workflow": workflow,
                                                "tool_calls": captures.copy(),
                                                "sql_usage": sql_usage,
                                                "agent_usage": agent_usage,
                                                "chat_requests": sql_calls + agent_calls,
                                                "contract_passed": passed,
                                                "duration_ms": round(
                                                    (time.perf_counter() - start) * 1000, 3
                                                ),
                                            }
                                        )
                                        write(output, report)
                finally:
                    transaction.rollback()
                    app.dependency_overrides.clear()
                report["database_rollback_verified"] = counts(connection) == before
                if not report["database_rollback_verified"]:
                    raise ValueError("Rollback mismatch")
        report["state"] = "complete"
    except Exception as error:
        report["state"], report["failure_class"] = "failed", type(error).__name__
        raise
    finally:
        report["embedding_requests"] = observation.requests
        write(output, report)
        engine.dispose()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=["baseline", "development", "legacy-development"], required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.mode, args.output)
        print(
            json.dumps({"state": result["state"], "total_chat_tokens": result["total_chat_tokens"]})
        )
    except Exception as error:
        print(json.dumps({"state": "failed", "failure_class": type(error).__name__}))
        raise SystemExit(1) from None
