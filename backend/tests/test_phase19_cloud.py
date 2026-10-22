from datetime import UTC, datetime
from types import SimpleNamespace

from app.phase19_cloud import matches, service_events


def test_trace_correlation_requires_timing_status_and_model_counts():
    saved = SimpleNamespace(
        start_time=datetime(2026, 10, 21, tzinfo=UTC),
        extra={
            "metadata": {
                "duration_ms": 2000,
                "status": "success",
                "agent_model_calls": 2,
                "sql_model_calls": 1,
            }
        },
    )
    event = {
        "timestamp": "2026-10-21T00:00:02.001+00:00",
        "duration_ms": 2000,
        "status": "success",
        "workflow": {"agent": {"model_calls": 2}, "sql_model_calls": 1},
    }
    assert matches(saved, event)
    assert not matches(saved, {**event, "status": "clarification"})
    assert not matches(saved, {**event, "timestamp": "2026-10-21T00:01:00+00:00"})
    assert not matches(
        saved, {**event, "workflow": {"agent": {"model_calls": 3}, "sql_model_calls": 1}}
    )


def test_service_parser_does_not_collect_non_evidence_messages(tmp_path):
    path = tmp_path / "service.log"
    path.write_text(
        'prefix {"event":"analysis_completed","analysis_id":"synthetic"}\n'
        'prefix {"event":"http_request","body":"DO NOT COLLECT"}\n'
        "not json\n",
        encoding="utf-8",
    )
    assert service_events(path) == [{"event": "analysis_completed", "analysis_id": "synthetic"}]
