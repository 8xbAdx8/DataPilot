import json
import time

import pytest
from langchain_core.messages import ToolMessage

from app.analysis_agent import PlannerContext, plan_analysis
from app.metric21_acceptance import check_prior_usage


def test_actual_tool_results_are_observable_but_history_excludes_arguments(
    client, session, settings, auth_user, monkeypatch
):
    dataset = client.post(
        "/api/datasets", files={"file": ("synthetic.csv", b"fee\n10\n20\n")}
    ).json()
    context = PlannerContext(session, auth_user.id, dataset["id"], settings, time.monotonic() + 30)
    seen = []
    original = context.record_tool

    def observe(name, arguments, response):
        seen.append((name, arguments, json.loads(response.content)))
        original(name, arguments, response)

    monkeypatch.setattr(context, "record_tool", observe)
    plan_analysis(context, "fee 总和")
    assert seen[0][0] == "inspect_dataset_schema"
    assert seen[0][2]["fields"][0]["name"] == "fee"
    assert len(context.tool_diagnostics) == 2
    assert "fee" not in json.dumps(context.metadata()["tool_diagnostics"])
    context.record_tool(
        "get_metric_definition",
        {"name": "PRIVATE-NAME"},
        ToolMessage(
            content=json.dumps({"status": "no_match", "citations": [], "secret": "PRIVATE-BODY"}),
            tool_call_id="probe",
        ),
    )
    assert "PRIVATE" not in json.dumps(context.tool_diagnostics)
    assert context.tool_diagnostics[-1]["result_status"] == "no_match"


@pytest.mark.parametrize(
    "record",
    [
        {"state": "complete", "unknown_usage": True},
        {"state": "claimed", "unknown_usage": False},
    ],
)
def test_uncertain_previous_live_usage_blocks_even_a_new_output_path(tmp_path, record):
    (tmp_path / "metric21-legacy-live-v1.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="uncertain usage"):
        check_prior_usage(tmp_path)
