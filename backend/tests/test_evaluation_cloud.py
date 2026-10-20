import json

import httpx
import pytest

from app.config import Settings
from app.errors import AppError
from app.evaluate import load_cases
from app.evaluation_cloud import publish_report

SECRET = "private-question-sql-row-path-password-sentinel"


def report():
    record = {
        "id": "count",
        "correct": True,
        "result_correct": True,
        "safety_ok": None,
        "recall_at_k": None,
        "false_refusal": False,
        "sql_calls": 1,
        "agent_calls": 2,
        "tool_calls": 2,
        "tokens": None,
        "started_at": "2026-10-20T10:00:00+00:00",
        "ended_at": "2026-10-20T10:00:01+00:00",
        "question": SECRET,
        "sql": SECRET,
        "result": {"rows": [SECRET]},
        "error_message": SECRET,
    }
    return {
        "mode": "controlled-offline-not-model-accuracy",
        "records": {"baseline": [record.copy()], "enhanced": [record.copy()]},
        "summary": {"secret": SECRET},
    }


def config():
    return Settings(
        _env_file=None, datapilot_langsmith_enabled=True, langsmith_api_key="test-only-key"
    )


def test_cloud_metrics_require_explicit_config_before_transport():
    for settings in (
        Settings(_env_file=None),
        Settings(_env_file=None, datapilot_langsmith_enabled=True, langsmith_api_key=""),
    ):
        with pytest.raises(AppError) as error:
            publish_report(
                report(),
                load_cases(),
                settings,
                transport=httpx.MockTransport(lambda _: pytest.fail("No network")),
            )
        assert error.value.detail["code"] == "langsmith_not_configured"


def test_actual_http_payload_groups_fixed_cases_and_only_sends_scores():
    bodies = []

    def handle(request):
        assert request.url.path == "/api/v1/datasets/upload-experiment"
        assert request.headers["x-api-key"] == "test-only-key"
        body = json.loads(request.content)
        assert (
            SECRET not in request.content.decode()
            and "test-only-key" not in request.content.decode()
        )
        bodies.append(body)
        return httpx.Response(
            200,
            json={
                "dataset": {"id": "synthetic-dataset"},
                "experiment": {"id": "synthetic-experiment"},
            },
        )

    assert publish_report(
        report(), load_cases(), config(), transport=httpx.MockTransport(handle)
    ) == {"status": "acknowledged", "experiments": 2}
    assert len(bodies) == 2 and bodies[0]["dataset_name"] == bodies[1]["dataset_name"]
    assert bodies[0]["results"][0]["row_id"] == bodies[1]["results"][0]["row_id"]
    for body in bodies:
        row = body["results"][0]
        assert (
            row["inputs"] == {"case_id": "count"}
            and row["actual_outputs"] == row["expected_outputs"] == {}
        )
        assert {score["key"] for score in row["evaluation_scores"]} == {
            "correct",
            "result_correct",
            "false_refusal",
        }
        assert "tokens" not in row["run_metadata"]


@pytest.mark.parametrize(
    "field,value", [("id", SECRET), ("correct", SECRET), ("ended_at", "invalid")]
)
def test_untrusted_case_names_scores_or_timestamps_never_reach_network(field, value):
    data = report()
    data["records"]["enhanced"][0][field] = value
    with pytest.raises(AppError) as error:
        publish_report(
            data,
            load_cases(),
            config(),
            transport=httpx.MockTransport(lambda _: pytest.fail("Invalid report exported")),
        )
    assert error.value.detail["code"] == "evaluation_publish_invalid"


def test_remote_failure_redacts_body_and_does_not_retry():
    calls = []

    def failure(request):
        calls.append(request)
        return httpx.Response(500, text=SECRET)

    data = report()
    with pytest.raises(AppError) as error:
        publish_report(data, load_cases(), config(), transport=httpx.MockTransport(failure))
    assert SECRET not in str(error.value.detail) and len(calls) == 1
    assert data["records"]["baseline"][0]["correct"]  # Local scores are untouched.
