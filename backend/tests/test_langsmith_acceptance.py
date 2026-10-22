import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.langsmith_acceptance import assert_private_trace, feedback_matches, publication_preview


@pytest.mark.parametrize(
    "actual,expected,match",
    [
        (0.3333, 1 / 3, True),
        (1 / 3, 1 / 3, True),
        (0.9167, 11 / 12, True),
        (0.9168, 11 / 12, False),
        (None, 1, False),
        (True, 1, False),
        (float("nan"), 1, False),
    ],
)
def test_cloud_quantization_does_not_change_the_authoritative_local_score(actual, expected, match):
    assert feedback_matches(actual, expected) is match


def report():
    saved = json.loads(
        (Path(__file__).parents[2] / "docs/rag-baseline-development.json").read_bytes()
    )
    saved["configuration"].update(
        rag_strategy="vector", rag_reranker="none", reranker_min_score=0.5
    )
    return saved


def test_preview_only_ids_numeric_labels_and_safe_models_no_synthetic_bodies():
    original = report()
    original["configuration"].update(api_key="NEVER_EXPORT", llm_model="NEVER_EXPORT")
    preview = publication_preview(original)
    text = json.dumps(preview, ensure_ascii=False)
    assert "NEVER_EXPORT" not in text
    assert "SUM(" not in text and "excerpt" not in text and "销售" not in text
    assert all(set(x["inputs"]) == {"sample_id"} for x in preview["examples"])
    assert preview["configuration"]["llm_model"] == "custom"
    bad = copy.deepcopy(original)
    bad["configuration"]["embedding_dimensions"] = "NEVER_EXPORT"
    with pytest.raises(ValueError):
        publication_preview(bad)


def test_readback_privacy_checks_body_runtime_timing_and_missing_nodes():
    clean = SimpleNamespace(
        name="plan",
        child_runs=[],
        inputs={},
        outputs={},
        serialized=None,
        extra={"metadata": {"status": "success"}},
        start_time=1,
        end_time=2,
    )
    assert assert_private_trace(clean, {"plan"}) == ["plan"]
    child = copy.deepcopy(clean)
    child.name = "inspect_dataset_schema"
    clean.child_runs = [child]
    assert set(assert_private_trace(clean, {"plan", child.name})) == {"plan", child.name}
    clean.extra["metadata"]["ls_run_depth"] = 0
    child.extra["metadata"]["ls_run_depth"] = 1
    assert assert_private_trace(clean, {"plan"})
    for field, value in (
        ("inputs", {"question": "secret"}),
        ("outputs", {"rows": "secret"}),
        ("extra", {"runtime": {"env": "secret"}}),
        ("extra", {"metadata": {"password": "secret"}}),
        ("end_time", None),
    ):
        changed = copy.deepcopy(clean)
        setattr(changed, field, value)
        with pytest.raises(ValueError):
            assert_private_trace(changed, {"plan"})
    with pytest.raises(ValueError):
        assert_private_trace(clean, {"execute"})
    for invalid in (True, "0", -1, 99):
        changed = copy.deepcopy(clean)
        changed.extra["metadata"]["ls_run_depth"] = invalid
        with pytest.raises(ValueError):
            assert_private_trace(changed, {"plan"})


def test_actual_api_succeeds_when_cloud_transport_is_unavailable(client, settings, monkeypatch):
    from app import tracing

    settings.datapilot_langsmith_enabled = True
    settings.langsmith_api_key = "unit-test-placeholder"

    def unavailable(*args):
        raise RuntimeError("PRIVATE_TRANSPORT_ERROR")

    monkeypatch.setattr(tracing, "client_for", unavailable)
    # Synchronously exercise the real error-catching sender without background races.
    monkeypatch.setattr(
        tracing,
        "_enqueue",
        lambda s, run, status, duration: tracing.deliver(
            s, tracing.trace_payload(run, status, duration)
        ),
    )
    dataset = client.post(
        "/api/datasets", files={"file": ("synthetic.csv", b"value\n10\n20\n")}
    ).json()["id"]
    response = client.post(f"/api/datasets/{dataset}/analyses", json={"question": "行数"})
    assert response.status_code == 201
    assert response.json()["status"] == "success"
    assert list(response.json()["result"]["rows"][0].values()) == [2]
    assert "PRIVATE_TRANSPORT_ERROR" not in response.text


def test_controlled_rejection_sends_only_actual_failed_graph_metadata(settings, monkeypatch):
    from app import langsmith_acceptance

    captured = []

    def confirmed(local, rows):
        captured.extend(rows)
        return {"status": "offline-test-double"}

    monkeypatch.setattr(langsmith_acceptance, "confirm_trace", confirmed)
    result = langsmith_acceptance.rejection_trace(settings)
    assert result["zero_execution"]
    assert captured[0]["extra"]["metadata"]["status"] == "rejected"
    assert captured[0]["extra"]["metadata"]["sql_validation"] == "rejected"
    assert "execute" not in {row["name"] for row in captured}
    assert "DROP" not in str(captured)
