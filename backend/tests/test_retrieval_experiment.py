import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings
from app.main import app
from app.models import KnowledgeChunk, KnowledgeDocument
from app.providers import SqlProposal, get_provider


def test_defaults_and_configuration_do_not_enable_experiments():
    s = Settings(_env_file=None)
    assert (s.rag_evidence_policy, s.rag_strategy, s.rag_min_similarity, s.rag_reranker) == (
        "legacy",
        "vector",
        0.70,
        "none",
    )
    assert s.rag_experiment_mode == "off" and not s.rag_experiment_synthetic
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_experiment_mode="shadow")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            rag_experiment_mode="enabled",
            rag_experiment_synthetic=True,
            datapilot_langsmith_enabled=True,
        )


@pytest.fixture
def fact(client, settings, session, monkeypatch):
    settings.embedding_provider = "fake"
    settings.agent_enabled = False
    settings.sql_repair_enabled = False
    settings.knowledge_requests_per_window = 100
    target = client.post(
        "/api/datasets", files={"file": ("synthetic.csv", b"state,fee\nclosed,10\nopen,20\n")}
    ).json()
    body = json.dumps(
        {
            "version": 1,
            "subject": "验收规则",
            "required_fields": ["state"],
            "facts": [{"aspect": "有效状态", "answer": "state为closed"}],
        }
    )
    document = client.post(
        f"/api/datasets/{target['id']}/knowledge",
        json={"title": "验收规则", "source_type": "business_rule", "content": body},
    ).json()
    chunk = session.scalar(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == document["id"])
    )
    source = {
        "chunk_id": chunk.id,
        "document_id": document["id"],
        "chunk_index": 0,
        "title": document["title"],
        "excerpt": body,
        "source_type": "business_rule",
        "similarity": 0.8,
    }
    monkeypatch.setattr(
        "app.analyses.retrieve_knowledge",
        lambda *_a, **_k: {
            "status": "retrieved",
            "source": "fake",
            "used": False,
            "citations": [source],
            "candidate_stages": [{"stage": "vector", "candidates": [source]}],
        },
    )

    class Proposal:
        calls = 0
        sql = "SELECT COUNT(*) AS count FROM data WHERE state='closed'"

        def generate_with_context(self, *_):
            self.calls += 1
            return SqlProposal(sql=self.sql, explanation="Synthetic controlled SQL")

        def generate(self, *_):
            self.calls += 1
            return SqlProposal(sql=self.sql, explanation="Synthetic controlled SQL")

    provider = Proposal()
    app.dependency_overrides[get_provider] = lambda: provider
    return target, document, source, provider


def analyze(client, fact, question="验收规则有效状态的行数"):
    return client.post(f"/api/datasets/{fact[0]['id']}/analyses", json={"question": question})


def activate(settings, session, mode):
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("isolated PostgreSQL experiment required")
    settings.rag_experiment_mode = mode
    settings.rag_experiment_synthetic = True


@pytest.mark.parametrize(
    "question", ["验收规则有效状态的行数", "验收规则未知价格", "未登记含糊业务值"]
)
def test_shadow_answer_and_rollback_are_identical(
    client, settings, session, fact, caplog, question
):
    baseline = analyze(client, fact, question).json()
    activate(settings, session, "shadow")
    shadow = analyze(client, fact, question).json()
    assert baseline["status"] == shadow["status"] == "success"
    assert baseline["sql"] == shadow["sql"]
    assert baseline["result"] == shadow["result"]
    experiment = shadow["telemetry"]["workflow"]["rag"]["experiment"]
    assert experiment["extra_embedding_http"] == 0 and experiment["mode"] == "shadow"
    settings.rag_experiment_mode = "off"
    rollback = analyze(client, fact, question).json()
    assert rollback["result"] == baseline["result"]
    assert "experiment" not in rollback["telemetry"]["workflow"]["rag"]
    logs = [json.loads(r.message) for r in caplog.records if '"retrieval_experiment"' in r.message]
    assert logs
    for log in logs:
        assert log["mode"] == "shadow" and log["extra_embedding_http"] == 0
        assert not any(k in log for k in ("query", "excerpt", "sql", "owner_id", "dataset_id"))
    # Explicit synthetic local acceptance only. Ordinary CI never writes evidence.
    import hashlib
    import os

    if os.getenv("PHASE28_RECORD") == "1":
        from app.config import ROOT
        from app.rag24_evaluate import write_new

        write_new(
            ROOT / "docs/phase28" / (hashlib.sha256(question.encode()).hexdigest()[:12] + ".json"),
            {
                "question": question,
                "provider": "controlled Mock; not real model evidence",
                "baseline": {
                    "status": baseline["status"],
                    "sql": baseline["sql"],
                    "result": baseline["result"],
                },
                "shadow": experiment,
                "rollback_equal": rollback["result"] == baseline["result"],
                "selected_used_snapshots": shadow["telemetry"]["workflow"]["rag"]["evidence"],
            },
        )


@pytest.mark.parametrize(
    "sql",
    ["DROP TABLE data", "SELECT secret FROM data", "SELECT * FROM unknown", "SELECT 1; SELECT 2"],
)
def test_enabled_path_keeps_sql_guard(client, settings, session, fact, monkeypatch, sql):
    activate(settings, session, "enabled")
    fact[3].sql = sql
    monkeypatch.setattr("app.analyses.execute_query", lambda *_: pytest.fail("unsafe execution"))
    result = analyze(client, fact)
    assert result.status_code in {400, 403, 422}, result.text


def test_no_evidence_or_mutated_source_never_falls_back(
    client, settings, session, fact, monkeypatch
):
    activate(settings, session, "enabled")
    result = analyze(client, fact, "验收规则未知价格").json()
    assert result["status"] == "clarification" and fact[3].calls == 0
    document = session.get(KnowledgeDocument, fact[1]["id"])
    document.status = "failed"
    session.commit()
    result = analyze(client, fact).json()
    assert result["status"] == "clarification" and fact[3].calls == 0


def test_synthetic_flag_alone_cannot_enable_business_database(client, settings, session, fact):
    settings.rag_experiment_mode = "enabled"
    settings.rag_experiment_synthetic = False
    response = analyze(client, fact)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "experiment_isolation_required"


def test_experiment_error_does_not_fall_back(client, settings, session, fact, monkeypatch):
    from app.errors import AppError

    activate(settings, session, "enabled")

    def fail(*_):
        raise AppError(503, "synthetic_experiment_failure", "受控失败")

    monkeypatch.setattr("app.retrieval_experiment.candidate", fail)
    response = analyze(client, fact)
    assert response.status_code == 503 and fact[3].calls == 0


@pytest.mark.parametrize("bad_filter", [False, True])
def test_catalog_metric_still_enforces_mandatory_and_filter(
    client, settings, session, monkeypatch, bad_filter
):
    from pathlib import Path

    activate(settings, session, "enabled")
    settings.embedding_provider = "fake"
    settings.agent_enabled = False
    settings.sql_repair_enabled = False
    fixtures = Path(__file__).parents[1] / "app/evals/phase25"
    target = client.post(
        "/api/datasets", files={"file": ("services.csv", (fixtures / "services.csv").read_bytes())}
    ).json()
    doc = json.loads((fixtures / "development-v1.json").read_bytes())["documents"][0]
    assert (
        client.post(
            f"/api/datasets/{target['id']}/knowledge",
            json={k: doc[k] for k in ("title", "content", "source_type")},
        ).status_code
        == 201
    )
    monkeypatch.setattr(
        "app.analyses.retrieve_knowledge",
        lambda *_a, **_k: {"status": "no_match", "source": "fake", "used": False, "citations": []},
    )
    sql = "SELECT SUM(fee)-SUM(cost) AS net FROM data WHERE state='closed'"
    if bad_filter:
        sql += " OR fee > 0"
        monkeypatch.setattr("app.analyses.execute_query", lambda *_: pytest.fail("filter bypass"))

    class Proposal:
        def generate_with_context(self, *_):
            return SqlProposal(sql=sql, explanation="Synthetic metric contract test")

    app.dependency_overrides[get_provider] = lambda: Proposal()
    result = client.post(
        f"/api/datasets/{target['id']}/analyses",
        json={"question": json.loads(doc["content"])["name"]},
    )
    if bad_filter:
        assert result.status_code in {403, 422}, result.text
    else:
        assert result.status_code == 201, result.text
        assert list(result.json()["result"]["rows"][0].values()) == [25]
