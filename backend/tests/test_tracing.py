import json
from datetime import UTC, datetime

import pytest
import requests
from langsmith import Client

from app import tracing
from app.analysis_graph import AnalysisRun, execute_analysis_graph
from app.config import Settings
from app.providers import MockProvider

SECRET = "private-sentinel-question-row-schema-sql-password-path"


def make_run(**overrides):
    settings = Settings(_env_file=None, **overrides)
    run = AnalysisRun(
        MockProvider(),
        settings,
        {},
        lambda _: {"columns": [], "rows": [{"value": SECRET}], "truncated": False},
        "行数",
        [{"name": "private_column", "type": "INTEGER"}],
    )
    execute_analysis_graph(run)
    run.question = SECRET
    run.sql = SECRET
    run.plan = {"metrics": [SECRET]}
    run.rag = {"status": "retrieved", "used": True, "citations": [{"excerpt": SECRET}]}
    now = datetime.now(UTC)
    run.agent_spans = [
        {"name": "inspect_dataset_schema", "start": now, "end": now, "status": "success"}
    ]
    run.agent_metrics = {"model_calls": 2, "tool_calls": 1, "payload": SECRET}
    return run


def test_default_and_missing_key_do_not_create_client_or_queue(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(tracing, "client_for", lambda *_: pytest.fail("Client must stay disabled"))
    monkeypatch.setattr(tracing, "_enqueue", lambda *_: pytest.fail("Must not queue"))
    for settings in (
        Settings(_env_file=None),
        Settings(_env_file=None, datapilot_langsmith_enabled=True, langsmith_api_key=""),
    ):
        assert tracing.export_trace(settings, make_run(), "success", 10) == "disabled"


def test_actual_sdk_serialized_batch_contains_only_metadata(monkeypatch):
    bodies = []
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_METADATA", json.dumps({"private": SECRET}))
    monkeypatch.setenv("LANGCHAIN_REVISION_ID", SECRET)

    def request(self, method, pathname, **kwargs):
        assert method == "POST" and pathname.endswith("/runs/batch")
        bodies.append(json.loads(kwargs["request_kwargs"]["data"]))
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(Client, "request_with_retries", request)
    run = make_run(datapilot_langsmith_enabled=True, langsmith_api_key="unit-test-placeholder")
    rows = tracing.trace_payload(run, "success", 123)
    # Defense in depth: test Client hide_inputs/outputs/metadata too, not just builder.
    rows[0]["inputs"] = {"question": SECRET}
    rows[0]["outputs"] = {"rows": SECRET}
    rows[0]["extra"]["metadata"]["password"] = SECRET
    tracing.deliver(run.settings, rows)
    assert len(bodies) == 1
    encoded = json.dumps(bodies)
    assert SECRET not in encoded and "unit-test-placeholder" not in encoded
    assert "private_column" not in encoded and "runtime" not in encoded
    payload = bodies[0]["post"]
    root = next(r for r in payload if r["name"] == "datapilot.analysis")
    assert root["inputs"] == {} and root["outputs"] == {}
    assert root["extra"]["metadata"]["duration_ms"] == 123
    assert root["extra"]["metadata"]["rag_used"] is True
    assert {r["name"] for r in payload} >= {"generate", "execute", "inspect_dataset_schema"}
    assert all(r["trace_id"] == root["id"] for r in payload)
    assert all(r["parent_run_id"] == root["id"] for r in payload if r != root)
    assert all(r["start_time"] <= r["end_time"] for r in payload)


def test_export_failure_cannot_change_analysis_and_never_logs_remote_body(monkeypatch, caplog):
    monkeypatch.setattr(tracing, "_enqueue", lambda *_: (_ for _ in ()).throw(RuntimeError(SECRET)))
    run = make_run(datapilot_langsmith_enabled=True, langsmith_api_key="unit-test-placeholder")
    assert tracing.export_trace(run.settings, run, "success", 1) == "error"
    assert run.result["rows"] == [{"value": SECRET}]
    assert SECRET not in caplog.text
    record = __import__("logging").LogRecord("langsmith.client", 30, "", 0, SECRET, (), None)
    tracing.SafeSDKLogs().filter(record)
    assert SECRET not in record.getMessage()


def test_queue_is_bounded_and_reports_drop_without_network(monkeypatch):
    from queue import Queue

    queue = Queue(maxsize=1)
    queue.put((None, []))
    monkeypatch.setattr(tracing, "_queue", queue)
    monkeypatch.setattr(tracing, "_started", True)
    run = make_run(datapilot_langsmith_enabled=True, langsmith_api_key="unit-test-placeholder")
    assert tracing.export_trace(run.settings, run, "success", 1) == "dropped"
    assert queue.qsize() == 1


def test_tool_and_model_spans_are_children_of_actual_plan_node():
    run = make_run()
    now = datetime.now(UTC)
    run.node_spans.append({"name": "plan", "start": now, "end": now, "status": "success"})
    run.agent_spans.append({"name": SECRET, "start": now, "end": now, "status": "success"})
    rows = tracing.trace_payload(run, "error", 10)
    plan = next(row for row in rows if row["name"] == "plan")
    tool = next(row for row in rows if row["name"] == "inspect_dataset_schema")
    assert tool["parent_run_id"] == plan["id"]
    assert tool["dotted_order"].startswith(plan["dotted_order"] + ".")
    assert all(row["name"] != SECRET for row in rows)
    assert rows[0]["error"] == "analysis_failed"


def test_unsafe_endpoint_and_arbitrary_metadata_are_rejected():
    for endpoint in (
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com?key=secret",
    ):
        with pytest.raises(ValueError):
            Settings(_env_file=None, langsmith_endpoint=endpoint)
    assert (
        tracing.safe_metadata(
            {"status": SECRET, "duration_ms": True, "sql_model_calls": -1, "question": SECRET}
        )
        == {}
    )
