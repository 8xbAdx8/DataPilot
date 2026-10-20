"""Opt-in real chat acceptance; never labels missing Embedding/cloud as passed."""

import json
import os
import time
from pathlib import Path

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from sqlalchemy import select

from app import analysis_agent
from app.config import Settings, get_settings
from app.main import app
from app.models import AuditEvent


class SyntheticCallEvidence(BaseCallbackHandler):
    """Observe actual provider replies, without request headers or message content."""

    def __init__(self):
        self.started = {}
        self.replies = []

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self.started[run_id] = time.perf_counter()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.started[run_id] = time.perf_counter()

    def on_llm_end(self, response, *, run_id, **kwargs):
        for generations in response.generations:
            for generation in generations:
                message = generation.message
                calls = []
                for call in message.tool_calls:
                    # Validate the native response against the same tool contracts.
                    contracts = {
                        "inspect_dataset_schema": analysis_agent.EmptyInput,
                        "get_profile_summary": analysis_agent.EmptyInput,
                        "request_clarification": analysis_agent.ClarificationInput,
                        "search_business_knowledge": analysis_agent.KnowledgeQuery,
                        "get_metric_definition": analysis_agent.MetricQuery,
                        "AnalysisPlan": analysis_agent.AnalysisPlan,
                    }
                    contract = contracts.get(call["name"])
                    valid = contract is not None
                    if valid:
                        try:
                            contract.model_validate(call["args"])
                        except ValueError:
                            valid = False
                    calls.append({"name": call["name"], "contract_valid": valid})
                self.replies.append(
                    {
                        "model": message.response_metadata.get("model_name"),
                        "duration_ms": round(
                            (time.perf_counter() - self.started.get(run_id, time.perf_counter()))
                            * 1000
                        ),
                        "tool_calls": calls,
                        "usage": message.usage_metadata,
                    }
                )


@pytest.mark.live_llm
def test_real_agent_graph_sql_history_on_synthetic_sales(client, session, settings, monkeypatch):
    if os.environ.get("RUN_LIVE_INTEGRATION") != "1":
        pytest.skip("Real integration requires explicit RUN_LIVE_INTEGRATION=1")
    assert session.bind.dialect.name == "postgresql", "Use an independent PostgreSQL test DB"
    live = Settings(data_dir=settings.data_dir)
    assert live.llm_provider != "mock" and live.llm_api_key
    assert not live.datapilot_langsmith_enabled, "Cloud acceptance is a separate explicit step"
    assert live.embedding_provider == "none", "This partial-chain test expects RAG unavailable"
    app.dependency_overrides[get_settings] = lambda: live
    evidence = SyntheticCallEvidence()
    original = analysis_agent.get_agent_model

    def observed_model(config, deadline):
        model = original(config, deadline)
        assert model.__class__.__name__ == "ChatOpenAI"
        model.callbacks = [evidence]
        return model

    monkeypatch.setattr(analysis_agent, "get_agent_model", observed_model)
    uploaded = client.post(
        "/api/datasets",
        files={"file": ("synthetic-sales.csv", b"region,revenue\nEast,10\nWest,20\nEast,30\n")},
    )
    assert uploaded.status_code == 201
    dataset_id = uploaded.json()["id"]
    started = time.perf_counter()
    response = client.post(
        f"/api/datasets/{dataset_id}/analyses",
        json={
            "question": "先调用 inspect_dataset_schema 获取字段。按 region 分组计算 revenue 总和，"
            "输出 region 和 total 两列，按 region 升序，柱状图。"
            "口径就是 SUM(revenue)，无需业务知识。"
        },
    )
    assert response.status_code == 201
    actual = response.json()
    assert actual["status"] == "success"
    assert actual["result"]["rows"] == [
        {"region": "East", "total": 40},
        {"region": "West", "total": 20},
    ]
    assert actual["result"]["chart"]["kind"] == "bar"
    workflow = actual["telemetry"]["workflow"]
    assert workflow["nodes"] == [
        "prepare",
        "retrieve",
        "plan",
        "generate",
        "validate",
        "execute",
        "check_result",
    ]
    assert workflow["validations"] == [{"status": "approved", "attempt": 0}]
    assert workflow["rag"]["status"] == "not_configured"
    assert workflow["rag"]["citations"] == []
    assert workflow["agent"]["mode"] == "native_tools"
    assert "inspect_dataset_schema" in workflow["agent"]["tools_used"]
    assert any(
        call["name"] == "inspect_dataset_schema"
        for reply in evidence.replies
        for call in reply["tool_calls"]
    )
    assert all(c["contract_valid"] for r in evidence.replies for c in r["tool_calls"])
    history = client.get(f"/api/analyses/{actual['id']}")
    assert history.status_code == 200 and history.json()["sql"] == actual["sql"]
    exported = client.get(f"/api/analyses/{actual['id']}/export")
    assert exported.status_code == 200 and "East,40" in exported.text
    events = list(
        session.scalars(select(AuditEvent.event).where(AuditEvent.analysis_id == actual["id"]))
    )
    assert {"analysis_started", "analysis_succeeded"}.issubset(events)
    artifact = {
        "provider": live.llm_provider,
        "configured_model": live.llm_model,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "agent_native_replies": evidence.replies,
        "sql": actual["sql"],
        "rows": actual["result"]["rows"],
        "chart": actual["result"]["chart"],
        "telemetry": actual["telemetry"],
        "audit_events": sorted(events),
        "history_and_csv": "passed",
        "real_embedding": "blocked_missing_configuration",
        "langsmith_cloud": "disabled_missing_credentials",
        "full_real_rag_e2e": "blocked",
        "cost": None,
    }
    output = os.environ.get("PHASE9_EVIDENCE_PATH")
    if output:
        target = Path(output).resolve()
        allowed = (Path(__file__).resolve().parents[2] / "data" / "runtime").resolve()
        assert target.is_relative_to(allowed), "Evidence must remain in ignored runtime storage"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
