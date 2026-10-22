import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth import COOKIE, hash_password, token_hash
from app.errors import AppError
from app.evidence_snapshot import begin_snapshot, capture_sources
from app.main import app
from app.models import Analysis, AuditEvent, AuthSession, KnowledgeChunk, KnowledgeDocument, User
from app.providers import SqlProposal, get_provider
from app.tracing import evidence_metadata, safe_metadata, source_hash, trace_payload

FIXTURES = Path(__file__).parents[1] / "app/evals"
GOOD = (
    "SELECT division,SUM(fee)-SUM(labor) AS service_net FROM data "
    "WHERE state='closed' GROUP BY division ORDER BY division"
)


@pytest.fixture
def metric(client, settings, monkeypatch):
    settings.embedding_provider = "fake"
    settings.knowledge_requests_per_window = 100
    target = client.post(
        "/api/datasets",
        files={"file": ("snapshot.csv", (FIXTURES / "metric21-services.csv").read_bytes())},
    ).json()
    contract = json.loads((FIXTURES / "metric21-development-v1.json").read_bytes())["metric"]
    doc = client.post(
        f"/api/datasets/{target['id']}/knowledge",
        json={
            "title": contract["name"],
            "source_type": "metric_definition",
            "content": json.dumps(contract, ensure_ascii=False),
        },
    ).json()
    monkeypatch.setattr(
        "app.analyses.retrieve_knowledge",
        lambda *_a, **_k: {
            "status": "no_match",
            "source": "fake",
            "used": False,
            "citations": [],
            "candidate_stages": [{"stage": "vector", "candidates": []}],
        },
    )

    class Proposal:
        def generate_with_context(self, *_):
            return SqlProposal(sql=GOOD, explanation="Controlled synthetic proposal")

    app.dependency_overrides[get_provider] = lambda: Proposal()
    return target, doc, contract


def analyze(client, metric):
    return client.post(f"/api/datasets/{metric[0]['id']}/analyses", json={"question": "服务实收"})


@pytest.mark.parametrize("policy", ["legacy", "registered"])
def test_catalog_snapshot_and_history_replay_never_retrieve_again(
    client, session, settings, metric, monkeypatch, policy
):
    settings.rag_evidence_policy = policy
    response = analyze(client, metric)
    assert response.status_code == 201, response.text
    body = response.json()
    rag = body["telemetry"]["workflow"]["rag"]
    evidence = rag["evidence"]
    assert rag["candidate_status"] == "no_match"
    assert evidence["version"] == 1 and evidence["policy"] == policy
    source = evidence["source_snapshots"][0]
    assert source["document_id"] == metric[1]["id"]
    assert source["excerpt"] == metric[1]["content"][:800]
    assert evidence["source_origins"] == [
        {"chunk_id": source["chunk_id"], "origin": "registered_catalog"}
    ]
    for key in ("selected_source_ids", "context_source_ids", "used_source_ids"):
        assert evidence[key] == [source["chunk_id"]]
    assert evidence["sql_validation"] == evidence["metric_validation"] == "approved"
    assert evidence["execution"] == "success" and evidence["final_decision"] == "answer"
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.analysis_id == body["id"], AuditEvent.event == "analysis_succeeded"
        )
    )
    assert audit.details["used_source_count"] == 1
    # The owning analysis snapshot persists even if the original knowledge is deleted.
    assert client.delete(f"/api/knowledge/{metric[1]['id']}").status_code == 204
    monkeypatch.setattr(
        "app.analyses.retrieve_knowledge", lambda *_a, **_k: pytest.fail("history retrieved")
    )
    monkeypatch.setattr(
        "app.analyses.MetricRegistry", lambda *_a, **_k: pytest.fail("history resolved")
    )
    session.expire_all()
    assert client.get(f"/api/analyses/{body['id']}").json()["telemetry"] == body["telemetry"]
    rows = client.get(f"/api/analyses?dataset_id={metric[0]['id']}").json()
    assert rows[0]["telemetry"] == body["telemetry"]


def test_selected_source_without_context_is_not_confirmed(client, metric):
    class Plain:
        def generate(self, *_):
            return SqlProposal(sql=GOOD, explanation="No contextual provider method")

    app.dependency_overrides[get_provider] = lambda: Plain()
    response = analyze(client, metric)
    assert response.status_code == 201
    e = response.json()["telemetry"]["workflow"]["rag"]["evidence"]
    assert e["selected_source_ids"] and e["context_source_ids"] == []
    assert e["metric_validation"] == "approved" and e["execution"] == "success"
    assert e["used_source_ids"] == []


@pytest.mark.parametrize("kind", ["inapplicable", "ambiguous"])
def test_unusable_catalog_candidates_are_saved_without_becoming_context(
    client, metric, monkeypatch, kind
):
    contract = metric[2].copy()
    if kind == "inapplicable":
        contract["required_fields"] = [*contract["required_fields"], "absent_field"]
        response = client.put(
            f"/api/knowledge/{metric[1]['id']}",
            json={
                "title": contract["name"],
                "source_type": "metric_definition",
                "content": json.dumps(contract, ensure_ascii=False),
            },
        )
    else:
        contract["name"] = "另一口径"
        response = client.post(
            f"/api/datasets/{metric[0]['id']}/knowledge",
            json={
                "title": contract["name"],
                "source_type": "metric_definition",
                "content": json.dumps(contract, ensure_ascii=False),
            },
        )
    assert response.status_code in {200, 201}
    monkeypatch.setattr(
        "app.analyses.plan_analysis", lambda *_: pytest.fail("unusable definition planned")
    )
    body = analyze(client, metric).json()
    assert body["status"] == "clarification"
    e = body["telemetry"]["workflow"]["rag"]["evidence"]
    assert e["status"] == kind and len(e["source_snapshots"]) == (
        1 if kind == "inapplicable" else 2
    )
    assert e["eligible_source_ids"] == e["context_source_ids"] == e["used_source_ids"] == []


@pytest.mark.parametrize(
    "kind", ["unsafe_sql", "metric_contract_violation", "query_timeout", "bad_result"]
)
def test_failed_sql_or_executor_never_confirms_sources(client, metric, monkeypatch, kind):
    if kind in {"unsafe_sql", "metric_contract_violation"}:

        class Wrong:
            def generate_with_context(self, *_):
                return SqlProposal(
                    sql="DROP TABLE data" if kind == "unsafe_sql" else "SELECT SUM(fee) FROM data",
                    explanation="Controlled rejection",
                )

        app.dependency_overrides[get_provider] = lambda: Wrong()
        monkeypatch.setattr(
            "app.analyses.execute_query", lambda *_: pytest.fail("rejected SQL executed")
        )
    elif kind == "query_timeout":

        def timeout(*_):
            raise AppError(408, "query_timeout", "合成执行超时")

        monkeypatch.setattr("app.analyses.execute_query", timeout)
    else:
        monkeypatch.setattr("app.analyses.execute_query", lambda *_: {"columns": []})
    response = analyze(client, metric)
    assert response.status_code >= 400
    body = client.get("/api/analyses/" + response.json()["detail"]["analysis_id"]).json()
    e = body["telemetry"]["workflow"]["rag"]["evidence"]
    assert e["selected_source_ids"] and e["context_source_ids"]
    assert e["used_source_ids"] == []
    assert e["final_decision"] == (
        "reject" if kind in {"unsafe_sql", "metric_contract_violation"} else "error"
    )
    assert e["execution"] == (
        "not_requested" if kind in {"unsafe_sql", "metric_contract_violation"} else "error"
    )


def test_successful_context_without_metric_proof_does_not_confirm(
    client, session, settings, metric, monkeypatch
):
    settings.agent_enabled = True
    chunk = session.scalar(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == metric[1]["id"])
    )
    document = session.get(KnowledgeDocument, chunk.document_id)
    document.source_type = "field_alias"
    document.content = chunk.content = '{"alias":"收费","field":"fee"}'
    session.commit()
    source = {
        "chunk_id": chunk.id,
        "document_id": document.id,
        "chunk_index": 0,
        "title": document.title,
        "source_type": "field_alias",
        "excerpt": chunk.content,
        "similarity": 1.0,
    }
    monkeypatch.setattr(
        "app.analyses.retrieve_knowledge",
        lambda *_a, **_k: {
            "status": "retrieved",
            "source": "fake",
            "used": False,
            "citations": [source],
            "candidate_stages": [{"stage": "vector", "candidates": [source.copy()]}],
        },
    )

    class Native:
        def generate_with_context(self, *_):
            return SqlProposal(
                sql="SELECT SUM(fee) AS total_fee FROM data", explanation="Native aggregate"
            )

    app.dependency_overrides[get_provider] = lambda: Native()
    response = client.post(
        f"/api/datasets/{metric[0]['id']}/analyses", json={"question": "fee 总和"}
    )
    assert response.status_code == 201, response.text
    e = response.json()["telemetry"]["workflow"]["rag"]["evidence"]
    assert e["selected_source_ids"] and e["context_source_ids"]
    assert e["metric_validation"] == "not_requested" and e["execution"] == "success"
    assert e["used_source_ids"] == []


def test_old_history_is_returned_without_fabricated_version(client, session, metric, monkeypatch):
    legacy = {"workflow": {"rag": {"status": "no_match", "used": True, "citations": []}}}
    row = Analysis(
        dataset_id=metric[0]["id"],
        question="旧记录",
        provider="mock",
        model="mock",
        status="clarification",
        telemetry=legacy,
    )
    session.add(row)
    session.commit()
    monkeypatch.setattr(
        "app.analyses.MetricRegistry", lambda *_a, **_k: pytest.fail("legacy rehydrated")
    )
    assert client.get(f"/api/analyses/{row.id}").json()["telemetry"] == legacy


def test_snapshot_rejects_sibling_sources_and_history_other_user(
    client, session, auth_user, metric
):
    sibling = client.post("/api/datasets", files={"file": ("sibling.csv", b"value\n1\n")}).json()
    chunk = session.scalar(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == metric[1]["id"])
    )
    rag = {
        "status": "retrieved",
        "citations": [],
        "candidate_stages": [{"stage": "vector", "candidates": [{"chunk_id": chunk.id}]}],
    }
    begin_snapshot(rag, "legacy")
    result = capture_sources(
        session, auth_user.id, sibling["id"], rag, [{"chunk_id": chunk.id, "excerpt": "forged"}]
    )
    assert result == [] and chunk.id not in json.dumps(rag)


def test_history_survives_logout_and_login(client, session, auth_user, metric):
    password = secrets.token_urlsafe(24)
    auth_user.password_hash = hash_password(password)
    session.commit()
    body = analyze(client, metric).json()
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get(f"/api/analyses/{body['id']}").status_code == 401
    # The conftest shortcut cookie uses path '/', unlike the real '/api' cookie.
    client.cookies.clear()
    assert (
        client.post(
            "/api/auth/login", json={"username": auth_user.username, "password": password}
        ).status_code
        == 200
    )
    assert client.get(f"/api/analyses/{body['id']}").json()["telemetry"] == body["telemetry"]


def test_other_user_cannot_read_snapshot_or_dataset_history(client, session, metric):
    body = analyze(client, metric).json()
    other = User(username="evidence-other-" + secrets.token_hex(8), password_hash="unused")
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
    client.cookies.set(COOKIE, token)
    assert client.get(f"/api/analyses/{body['id']}").status_code == 404
    assert client.get(f"/api/analyses?dataset_id={metric[0]['id']}").json() == []
    assert client.get(f"/api/knowledge/{metric[1]['id']}").status_code == 404


def test_serialized_trace_contract_drops_bodies_and_uses_private_per_trace_salt(
    client, metric, monkeypatch
):
    captured = []

    def local_export(_settings, run, status, duration):
        captured.extend(
            [trace_payload(run, status, duration), trace_payload(run, status, duration)]
        )

    monkeypatch.setattr("app.analyses.export_trace", local_export)
    response = analyze(client, metric)
    assert response.status_code == 201
    text = json.dumps(captured, default=str)
    source = response.json()["telemetry"]["workflow"]["rag"]["evidence"]["source_snapshots"][0]
    for secret in [
        source["chunk_id"],
        source["document_id"],
        source["excerpt"],
        "服务实收",
        "SUM(fee)",
    ]:
        assert secret not in text
    a, b = [rows[0]["extra"]["metadata"] for rows in captured]
    assert a["candidate_status"] == "no_match" and a["catalog_source_count"] == 1
    assert (
        a["planned_source_count"] == a["context_source_count"] == a["confirmed_source_count"] == 1
    )
    assert a["evidence_sources"] != b["evidence_sources"]
    assert source_hash(source["chunk_id"], captured[0][0]["id"]) not in {
        row["source_hash"] for row in a["evidence_sources"]
    }
    assert a == safe_metadata(a)
    for rows in captured:
        assert all(row["inputs"] == row["outputs"] == {} for row in rows)
    dirty = {
        "evidence_sources": [{"phase": "context", "source_hash": "bad", "body": "secret"}],
        "evidence_status": "secret",
        "context_source_count": True,
        "confirmed_source_count": 97,
        "api_key": "secret",
    }
    assert safe_metadata(dirty) == {"evidence_sources": []}
    assert evidence_metadata({"evidence": {"version": 0, "used_source_ids": ["x"]}}, b"secret") == {
        "evidence_version": "unrecorded"
    }
    assert "planned_source_count" not in evidence_metadata({"evidence": {"version": 1}}, b"secret")


def test_storage_failure_is_not_a_successful_history_or_trace(client, session, metric, monkeypatch):
    captured = []
    commit = session.commit
    calls = 0

    def fail_final_commit():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SQLAlchemyError("controlled persistence failure")
        commit()

    monkeypatch.setattr(session, "commit", fail_final_commit)
    monkeypatch.setattr(
        "app.analyses.export_trace",
        lambda _settings, run, status, duration: captured.append(
            trace_payload(run, status, duration)
        ),
    )
    response = analyze(client, metric)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"
    metadata = captured[0][0]["extra"]["metadata"]
    assert metadata["status"] == metadata["final_decision"] == "error"
    session.expire_all()
    row = session.scalar(select(Analysis).where(Analysis.dataset_id == metric[0]["id"]))
    # Failed persistence cannot save the completed snapshot; maintenance handles stale runs.
    assert row.status == "running" and not row.telemetry
