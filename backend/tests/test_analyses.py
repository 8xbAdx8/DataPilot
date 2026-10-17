import csv
import io

import pytest
from sqlalchemy import select

from app.analyses import result_path
from app.errors import AppError
from app.main import app
from app.models import Analysis, AuditEvent
from app.providers import SqlProposal, get_provider


@pytest.fixture
def dataset(client):
    response = client.post(
        "/api/datasets",
        files={"file": ("sales.csv", b"region,revenue\nEast,10\nWest,20\nEast,30\n")},
    )
    assert response.status_code == 201
    return response.json()


class StaticProvider:
    def __init__(self, sql):
        self.sql, self.calls = sql, 0

    def generate(self, *_):
        self.calls += 1
        return SqlProposal(sql=self.sql, explanation="Test proposal")


def ask(client, dataset, question="按 region 统计 revenue 总和"):
    return client.post(f"/api/datasets/{dataset['id']}/analyses", json={"question": question})


def test_mock_full_api_flow_history_and_export(client, dataset, session):
    response = ask(client, dataset)
    assert response.status_code == 201, response.text
    analysis = response.json()
    assert analysis["provider"] == "mock"
    assert analysis["status"] == "success"
    assert analysis["result"]["rows"] == [
        {"region": "East", "metric": 40},
        {"region": "West", "metric": 20},
    ]
    assert analysis["result"]["chart"]["kind"] == "bar"
    assert analysis["summary"]["hypotheses"] == []
    assert analysis["duration_ms"] > 0
    history = client.get("/api/analyses", params={"dataset_id": dataset["id"]}).json()
    assert history[0]["id"] == analysis["id"]
    assert "result" not in history[0]  # Large rows are read from bounded local snapshots only.
    assert client.get(f"/api/analyses/{analysis['id']}").json() == analysis
    exported = client.get(f"/api/analyses/{analysis['id']}/export")
    assert exported.status_code == 200
    assert list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig")))) == [
        ["region", "metric"],
        ["East", "40"],
        ["West", "20"],
    ]
    events = session.scalars(
        select(AuditEvent).where(AuditEvent.analysis_id == analysis["id"])
    ).all()
    assert {event.event for event in events} == {"analysis_started", "analysis_succeeded"}


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE data",
        "SELECT * FROM datasets",
        "SELECT missing FROM data",
        "SELECT * FROM data; DELETE FROM data",
        "SELECT * FROM read_csv('/etc/passwd')",
    ],
)
def test_rejected_model_sql_is_never_executed_and_is_audited(
    client,
    dataset,
    session,
    monkeypatch,
    sql,
):
    provider = StaticProvider(sql)
    app.dependency_overrides[get_provider] = lambda: provider
    monkeypatch.setattr("app.analyses.execute_query", lambda *_: pytest.fail("Unsafe SQL executed"))
    response = ask(client, dataset)
    assert response.status_code == 422
    analysis_id = response.json()["detail"]["analysis_id"]
    saved = session.get(Analysis, analysis_id)
    assert saved.status == "rejected"
    assert saved.error_code == "unsafe_sql"
    assert provider.calls == 1  # No repair/retry bypass.
    events = session.scalars(select(AuditEvent).where(AuditEvent.analysis_id == analysis_id)).all()
    assert events[-1].details["reason"] == "unsafe_sql"
    assert client.get(f"/api/analyses/{analysis_id}/export").status_code == 409


def test_provider_timeout_is_saved_as_failure(client, dataset, session):
    class Unavailable:
        def generate(self, *_):
            raise AppError(504, "llm_timeout", "模型超时")

    app.dependency_overrides[get_provider] = lambda: Unavailable()
    response = ask(client, dataset)
    assert response.status_code == 504
    record = session.get(Analysis, response.json()["detail"]["analysis_id"])
    assert record.status == "error"
    assert record.sql is None


def test_row_limit_is_visible_in_snapshot_summary_and_csv(client, dataset, settings):
    settings.max_result_rows = 1
    response = ask(client, dataset, "预览")
    assert response.status_code == 201, response.text
    analysis = response.json()
    assert analysis["truncated"] is True
    assert len(analysis["result"]["rows"]) == 1
    assert "部分数据" in " ".join(analysis["summary"]["limitations"])
    exported = client.get(f"/api/analyses/{analysis['id']}/export")
    assert exported.headers["x-result-truncated"] == "true"


def test_expired_result_does_not_fabricate_successful_export(client, dataset, settings):
    analysis = ask(client, dataset, "行数").json()
    result_path(settings, analysis["id"]).unlink()
    detail = client.get(f"/api/analyses/{analysis['id']}").json()
    assert detail["result"] is None
    assert detail["result_expired"] is True
    assert client.get(f"/api/analyses/{analysis['id']}/export").status_code == 410


def test_csv_export_neutralizes_formula_strings(client):
    dataset = client.post(
        "/api/datasets", files={"file": ("formula.csv", b"text,value\n=1+1,-10\n@SUM(A1),20\n")}
    ).json()
    analysis = ask(client, dataset, "预览").json()
    exported = client.get(f"/api/analyses/{analysis['id']}/export")
    rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert rows[1] == ["'=1+1", "-10"]
    assert rows[2][0] == "'@SUM(A1)"


def test_invalid_question_and_history_id(client, dataset):
    assert ask(client, dataset, "  ").status_code == 422
    assert ask(client, dataset, "x" * 1001).status_code == 422
    assert client.get("/api/analyses/not-a-uuid").status_code == 422
    assert client.get("/api/analyses/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/analyses?limit=1001").status_code == 422
