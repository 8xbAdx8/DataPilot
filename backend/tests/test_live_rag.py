"""Synthetic-only live semantic acceptance. Ordinary runs make no external calls."""

import json
import math
import os
import secrets
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from test_live_integration import SyntheticCallEvidence

from app import analysis_agent
from app.auth import COOKIE, token_hash
from app.config import Settings, get_settings
from app.embeddings import CompatibleEmbeddingProvider
from app.errors import AppError
from app.knowledge import retrieve_knowledge
from app.main import app
from app.models import AuditEvent, AuthSession, KnowledgeChunk, KnowledgeDocument, User

SALES = b"region,revenue,cost,status\nEast,10,4,paid\nWest,20,8,paid\nEast,30,12,refund\n"
METRIC = {
    "title": "净贡献额",
    "source_type": "metric_definition",
    "content": "净贡献额：只统计 status='paid' 的订单，按 SUM(revenue)-SUM(cost) 计算。"
    "revenue 是收到的收入，cost 是发生的支出；退款订单不计入。",
}
PARAPHRASE = "只算已付款交易，把收到的钱扣除花出去的成本，剩下的数额怎样计算？"


def upload(client):
    response = client.post("/api/datasets", files={"file": ("synthetic-sales.csv", SALES)})
    assert response.status_code == 201
    return response.json()["id"]


def add(client, dataset_id, body=METRIC):
    response = client.post(f"/api/datasets/{dataset_id}/knowledge", json=body)
    assert response.status_code == 201
    saved = response.json()
    assert saved["status"] == "ready" and not saved["fake_embedding"]
    return saved


def search(client, dataset_id, query=PARAPHRASE):
    response = client.post(f"/api/datasets/{dataset_id}/knowledge/search", json={"query": query})
    assert response.status_code == 200
    found = response.json()
    assert found["status"] == "retrieved" and found["source"] == "provider"
    return found["citations"]


@pytest.fixture
def live_rag(client, settings, session, monkeypatch, request):
    if os.environ.get("RUN_LIVE_RAG") != "1":
        pytest.skip("Real semantic acceptance requires RUN_LIVE_RAG=1")
    live = Settings(data_dir=settings.data_dir)
    assert session.get_bind().dialect.name == "postgresql"
    assert os.environ.get("TEST_DATABASE_URL"), "Explicit independent test DB required"
    assert session.get_bind().engine.url.database != make_url(live.database_url).database
    assert live.embedding_provider in {"openai", "openai_compatible"}
    assert live.embedding_api_key and live.embedding_dimensions == 1536
    same_key = live.embedding_api_key == live.llm_api_key
    assert not same_key, "Configure an independent Embedding key before live acceptance"
    endpoint = urlsplit(live.embedding_base_url)
    endpoint_clean = bool(
        endpoint.scheme == "https"
        and endpoint.hostname
        and not (endpoint.username or endpoint.password or endpoint.query or endpoint.fragment)
    )
    assert endpoint_clean, "Embedding endpoint must use HTTPS without embedded credentials"
    assert not live.datapilot_langsmith_enabled, "LangSmith cloud is outside this acceptance"
    assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0006"
    column = session.scalar(
        text(
            "SELECT format_type(atttypid,atttypmod) FROM pg_attribute "
            "WHERE attrelid='knowledge_chunks'::regclass AND attname='embedding'"
        )
    )
    assert column == "vector(1536)"
    evidence = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
        "started_at": datetime.now(UTC).isoformat(),
        "status": "not_completed",
        "provider": live.embedding_provider,
        "model": live.embedding_model,
        "dimensions": live.embedding_dimensions,
        "endpoint_host": endpoint.hostname,
        "pgvector": session.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        ),
        "requests": [],
        "local_configuration_failures": [],
        "embedding_tokens": None,
        "cost": None,
    }
    original = CompatibleEmbeddingProvider.embed

    def observe(provider, texts):
        assert provider.transport is None, "Live acceptance forbids a fake HTTP transport"
        assert len(evidence["requests"]) < 12, "Acceptance remote request budget exceeded"
        started = time.perf_counter()
        try:
            vectors = original(provider, texts)
        except AppError as error:
            target = (
                evidence["local_configuration_failures"]
                if error.detail["code"] == "embedding_not_configured"
                else evidence["requests"]
            )
            target.append({"status": "error", "code": error.detail["code"]})
            raise
        assert all(len(v) == 1536 and math.isclose(math.hypot(*v), 1) for v in vectors)
        evidence["requests"].append(
            {
                "status": "success",
                "model_validated": provider.model,
                "input_count": len(texts),
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "vector_dimensions": [len(v) for v in vectors],
            }
        )
        return vectors

    monkeypatch.setattr(CompatibleEmbeddingProvider, "embed", observe)
    app.dependency_overrides[get_settings] = lambda: live
    started = time.perf_counter()
    try:
        yield live, evidence
    finally:
        evidence["duration_ms"] = round((time.perf_counter() - started) * 1000)
        target = Path(__file__).resolve().parents[2] / "data/runtime"
        target.mkdir(parents=True, exist_ok=True)
        (target / f"phase10-{request.node.name}.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )


@pytest.mark.live_llm
def test_real_semantic_retrieval_permissions_and_lifecycle(client, session, live_rag):
    live, evidence = live_rag
    dataset_id, sibling_id = upload(client), upload(client)
    own = add(client, dataset_id)
    count = len(evidence["requests"])
    repeated = client.post(f"/api/datasets/{dataset_id}/knowledge", json=METRIC)
    assert repeated.status_code == 200 and repeated.json()["duplicate"]
    assert repeated.json()["id"] == own["id"] and len(evidence["requests"]) == count
    sibling = add(client, sibling_id)
    other = User(username="live-rag-" + secrets.token_hex(8), password_hash="unused-fixture")
    session.add(other)
    session.flush()
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=other.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    session.commit()
    with TestClient(app, headers={"X-DataPilot-CSRF": "1"}) as foreign_client:
        foreign_client.cookies.set(COOKIE, token)
        foreign_dataset = upload(foreign_client)
        foreign = add(foreign_client, foreign_dataset)
    own_sources = search(client, dataset_id)
    assert {s["document_id"] for s in own_sources} == {own["id"]}
    assert {s["document_id"] for s in search(client, sibling_id)} == {sibling["id"]}
    for source in own_sources:
        chunk = session.get(KnowledgeChunk, source["chunk_id"])
        assert chunk is not None and chunk.document_id == own["id"]
        assert source["excerpt"] == chunk.content
        assert len(chunk.embedding) == 1536 and not chunk.embedding_space.startswith("fake/")
    for path in (f"/api/knowledge/{foreign['id']}",):
        assert client.get(path).status_code == 404
        assert client.delete(path).status_code == 404
    assert (
        client.post(
            f"/api/datasets/{foreign_dataset}/knowledge/search", json={"query": PARAPHRASE}
        ).status_code
        == 404
    )
    initial_ids = {s["chunk_id"] for s in own_sources}
    changed = {**METRIC, "content": "新版净贡献额只统计 status='paid' 的 SUM(revenue)，不扣成本。"}
    updated = client.put(f"/api/knowledge/{own['id']}", json=changed)
    assert updated.status_code == 200
    assert updated.json()["version"] == 2 and updated.json()["status"] == "ready"
    updated_sources = search(client, dataset_id, "净贡献额的新版计算规则是什么？")
    assert all(s["excerpt"] == changed["content"] for s in updated_sources)
    assert initial_ids.isdisjoint({s["chunk_id"] for s in updated_sources})
    assert all(session.get(KnowledgeChunk, chunk_id) is None for chunk_id in initial_ids)
    no_key = live.model_copy(update={"embedding_api_key": ""})
    app.dependency_overrides[get_settings] = lambda: no_key
    count = len(evidence["requests"])
    failed = client.post(f"/api/knowledge/{own['id']}/reindex").json()
    assert failed["status"] == "failed" and failed["error_code"] == "embedding_not_configured"
    assert len(evidence["requests"]) == count
    assert (
        retrieve_knowledge(session, own_owner(session, own), dataset_id, PARAPHRASE, live)["status"]
        == "no_documents"
    )
    app.dependency_overrides[get_settings] = lambda: live
    recovered = client.post(f"/api/knowledge/{own['id']}/reindex").json()
    assert recovered["status"] == "ready" and recovered["version"] == 4
    assert client.delete(f"/api/knowledge/{own['id']}").status_code == 204
    assert client.get(f"/api/knowledge/{own['id']}").status_code == 404
    assert (
        session.scalar(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == own["id"]))
        is None
    )
    after = client.post(
        f"/api/datasets/{dataset_id}/knowledge/search", json={"query": PARAPHRASE}
    ).json()
    assert after["status"] == "no_documents" and after["citations"] == []
    evidence.update(
        status="passed",
        semantic_query=PARAPHRASE,
        citations=own_sources,
        duplicate_update_delete_recovery="passed",
        owner_and_dataset_isolation="passed",
    )


def own_owner(session, document):
    return session.get(KnowledgeDocument, document["id"]).owner_id


@pytest.mark.live_llm
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_RAG_AGENT") != "1", reason="RUN_LIVE_RAG_AGENT=1 required"
)
def test_real_semantic_rag_reaches_native_agent_and_duckdb(client, session, live_rag, monkeypatch):
    live, evidence = live_rag
    assert live.agent_enabled and live.llm_provider != "mock" and live.llm_api_key
    dataset_id = upload(client)
    document = add(client, dataset_id)
    replies = SyntheticCallEvidence()
    factory = analysis_agent.get_agent_model

    def observe_model(settings, deadline):
        model = factory(settings, deadline)
        assert model.__class__.__name__ == "ChatOpenAI"
        model.callbacks = [replies]
        return model

    monkeypatch.setattr(analysis_agent, "get_agent_model", observe_model)
    response = client.post(
        f"/api/datasets/{dataset_id}/analyses",
        json={
            "question": "根据业务定义计算净贡献额，输出 net_contribution 单列。"
            "先调用 inspect_dataset_schema 获取字段，采用检索到的口径，不自行发明。"
        },
    )
    assert response.status_code == 201 and response.json()["status"] == "success"
    actual = response.json()
    assert actual["result"]["rows"] == [{"net_contribution": 18}]
    workflow = actual["telemetry"]["workflow"]
    assert workflow["rag"]["used"] and workflow["rag"]["source"] == "provider"
    assert {s["document_id"] for s in workflow["rag"]["citations"]} == {document["id"]}
    assert workflow["plan"]["knowledge_chunk_ids"]
    assert workflow["agent"]["mode"] == "native_tools"
    assert "inspect_dataset_schema" in workflow["agent"]["tools_used"]
    assert workflow["nodes"].index("validate") < workflow["nodes"].index("execute")
    assert all(v["status"] == "approved" for v in workflow["validations"])
    assert any(
        c["name"] == "inspect_dataset_schema" for r in replies.replies for c in r["tool_calls"]
    )
    assert all(c["contract_valid"] for r in replies.replies for c in r["tool_calls"])
    assert client.get(f"/api/analyses/{actual['id']}").json()["sql"] == actual["sql"]
    assert "18" in client.get(f"/api/analyses/{actual['id']}/export").text
    events = set(
        session.scalars(select(AuditEvent.event).where(AuditEvent.analysis_id == actual["id"]))
    )
    assert {"analysis_started", "analysis_succeeded"}.issubset(events)
    evidence.update(
        status="passed",
        sql=actual["sql"],
        rows=actual["result"]["rows"],
        telemetry=actual["telemetry"],
        native_agent_replies=replies.replies,
    )
