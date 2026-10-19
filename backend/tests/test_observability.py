import json
import logging
import uuid

import pytest

from app.main import app
from app.models import Analysis
from app.providers import SqlProposal, TokenUsage, get_provider


def events(caplog):
    return [
        json.loads(record.getMessage()) for record in caplog.records if record.name == "datapilot"
    ]


@pytest.mark.parametrize(
    "path,status",
    [
        ("/api/health/live", 200),
        ("/api/analyses/not-a-uuid", 422),
        ("/secret-unmatched-path?password=private-sentinel", 404),
    ],
)
def test_request_id_is_generated_and_log_never_contains_raw_path_or_query(
    client, caplog, path, status
):
    caplog.set_level(logging.INFO, logger="datapilot")
    response = client.get(path, headers={"X-Request-ID": "private-sentinel"})
    assert response.status_code == status
    trace = response.headers["x-request-id"]
    assert str(uuid.UUID(trace)) == trace
    record = events(caplog)[-1]
    assert record["request_id"] == trace and record["status_code"] == status
    assert record["duration_ms"] >= 0 and record["response_bytes"] == len(response.content)
    assert "private-sentinel" not in json.dumps(events(caplog))
    assert "secret-unmatched-path" not in json.dumps(events(caplog))


def test_csrf_and_auth_failures_also_have_request_ids_and_sanitized_logs(client, caplog):
    caplog.set_level(logging.INFO, logger="datapilot")
    response = client.post(
        "/api/auth/login",
        headers={"Origin": "https://private-sentinel.invalid"},
        json={"username": "private-sentinel", "password": "private-sentinel"},
    )
    assert response.status_code == 403 and response.headers["x-request-id"]
    client.cookies.clear()
    assert client.get("/api/datasets").status_code == 401
    assert "private-sentinel" not in json.dumps(events(caplog))
    assert events(caplog)[0]["route"] == "<unmatched>"  # Rejected before routing.


def test_unexpected_errors_return_safe_500_and_correlation_header(client, monkeypatch, caplog):
    def fail(*_):
        raise RuntimeError("private-sentinel database password")

    monkeypatch.setattr("app.main.get_settings", fail)
    response = client.get("/api/config")
    assert response.status_code == 500 and response.headers["x-request-id"]
    assert response.json()["detail"]["code"] == "internal_error"
    assert "private-sentinel" not in response.text + json.dumps(events(caplog))


def test_analysis_metadata_correlates_logs_and_history_without_logging_question_or_data(
    client, caplog
):
    uploaded = client.post(
        "/api/datasets",
        files={"file": ("private-sentinel.csv", b"text,value\nprivate-sentinel,12\n")},
    ).json()
    caplog.clear()
    response = client.post(
        f"/api/datasets/{uploaded['id']}/analyses", json={"question": "预览 private-sentinel"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["request_id"] == response.headers["x-request-id"]
    metrics = body["telemetry"]
    assert set(metrics["timings_ms"]) == {"provider", "sql_guard", "query", "persist"}
    assert all(isinstance(value, int) and value >= 0 for value in metrics["timings_ms"].values())
    assert metrics["usage"] == {
        "source": "mock",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    complete = next(record for record in events(caplog) if record["event"] == "analysis_completed")
    assert complete["analysis_id"] == body["id"] and complete["request_id"] == body["request_id"]
    assert complete["usage"] == metrics["usage"]
    assert "private-sentinel" not in json.dumps(events(caplog))
    assert "sql" not in complete and "rows" not in complete
    detail = client.get(f"/api/analyses/{body['id']}")
    assert detail.json()["request_id"] == body["request_id"]
    assert detail.headers["x-request-id"] != body["request_id"]


@pytest.mark.parametrize(
    "mode,stages,source",
    [
        ("provider_error", {"provider"}, "unavailable"),
        ("unsafe_sql", {"provider", "sql_guard"}, "provider"),
        ("query_error", {"provider", "sql_guard", "query"}, "provider"),
    ],
)
def test_failed_stages_record_only_actual_execution_and_known_usage(
    client, session, caplog, mode, stages, source, monkeypatch
):
    from app.errors import AppError

    class Provider:
        usage = (
            TokenUsage()
            if mode == "provider_error"
            else TokenUsage(
                source="provider", prompt_tokens=10, completion_tokens=3, total_tokens=13
            )
        )

        def generate(self, *_):
            if mode == "provider_error":
                raise AppError(504, "llm_timeout", "模型超时")
            return SqlProposal(
                sql="SELECT * FROM data" if mode == "query_error" else "DROP TABLE data",
                explanation="private-sentinel",
            )

    if mode == "query_error":

        def fail_query(*_):
            raise AppError(504, "query_timeout", "查询超时")

        monkeypatch.setattr("app.analyses.execute_query", fail_query)

    app.dependency_overrides[get_provider] = Provider
    dataset = client.post("/api/datasets", files={"file": ("safe.csv", b"a,b\n1,2\n")}).json()
    response = client.post(
        f"/api/datasets/{dataset['id']}/analyses", json={"question": "private-sentinel"}
    )
    assert response.status_code in {422, 504}
    saved = session.get(Analysis, response.json()["detail"]["analysis_id"])
    assert set(saved.telemetry["timings_ms"]) == stages
    assert saved.telemetry["usage"]["source"] == source
    assert "private-sentinel" not in json.dumps(events(caplog))


def test_legacy_records_serialize_empty_telemetry_without_fabricating_metrics(
    client, session, auth_user
):
    dataset = client.post("/api/datasets", files={"file": ("old.csv", b"a,b\n1,2\n")}).json()
    session.add(
        Analysis(
            dataset_id=dataset["id"], question="old", provider="mock", model="old", status="error"
        )
    )
    session.commit()
    row = client.get("/api/analyses").json()[0]
    assert row["request_id"] is None and row["telemetry"] == {}


def test_post_execution_storage_failure_still_logs_known_usage_and_does_not_claim_saved_success(
    client, session, monkeypatch, caplog
):
    from sqlalchemy.exc import SQLAlchemyError

    dataset = client.post("/api/datasets", files={"file": ("a.csv", b"a,b\n1,2\n")}).json()
    original = session.commit
    calls = 0

    def commit():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SQLAlchemyError("private-sentinel storage detail")
        original()

    monkeypatch.setattr(session, "commit", commit)
    response = client.post(f"/api/datasets/{dataset['id']}/analyses", json={"question": "行数"})
    assert response.status_code == 503
    record = next(row for row in events(caplog) if row["event"] == "analysis_completed")
    assert record["status"] == "error" and record["error_code"] == "storage_unavailable"
    assert record["usage"]["source"] == "mock"
    assert "private-sentinel" not in json.dumps(events(caplog))
