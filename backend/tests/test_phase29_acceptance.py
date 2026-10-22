import json

import duckdb
import httpx
import pytest

from app.phase29_acceptance import (
    FIXTURES,
    AdmissionTransport,
    corpus,
    provider_dependency,
)
from app.rag24_budget import BudgetStop


def test_human_review_groups_and_independent_hand_results():
    # Label integrity only, never strategy scoring or model calls.
    value = corpus()
    assert len(value["queries"]) == 50 and len({q["intent_group"] for q in value["queries"]}) == 50
    assert {
        kind: sum(q["kind"] == kind for q in value["queries"])
        for kind in {q["kind"] for q in value["queries"]}
    } == {"metric": 10, "structured": 7, "prose": 8, "no_answer": 10, "safety": 10, "complex": 5}
    connection = duckdb.connect()
    try:
        connection.execute(
            "CREATE TABLE data AS SELECT * FROM read_csv_auto(?)", [str(FIXTURES / "bookings.csv")]
        )
        for case in value["queries"]:
            if "reference_sql" in case:
                result = connection.execute(case["reference_sql"])
                assert [c[0] for c in result.description] == case["columns"], case["id"]
                assert [list(r) for r in result.fetchall()] == case["rows"], case["id"]
    finally:
        connection.close()


def test_pre_reservation_denial_remains_visible_when_api_catches_it(tmp_path):
    from app.trial_budget import TrialLedger

    ledger = TrialLedger(tmp_path / "trial.sqlite", {"test": True}, create=True)

    class Estimator:
        def estimate(self, *_):
            return 20001, 20001  # Stage and global cap refusal before HTTP.

    control, sent = {}, []
    transport = AdmissionTransport(
        ledger,
        Estimator(),
        stage=29,
        kind="chat",
        case="independent-test",
        channel="agent",
        provider="test",
        model="test",
        base_url="https://example.invalid",
        inner=httpx.MockTransport(lambda r: sent.append(r)),
        control=control,
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(BudgetStop):
            client.post("https://example.invalid/chat/completions", json={"model": "test"})
    assert control["denied"] == "trial_budget_preflight_denied" and not sent
    assert ledger.export()["requests"] == []
    ledger.close()


def test_provider_dependency_has_no_extra_request_parameters():
    import inspect

    dependency = provider_dependency(object())
    assert not inspect.signature(dependency).parameters


def test_freeze_assets_include_explicit_api_question_and_no_private_fields():
    value = corpus()
    case = next(q for q in value["queries"] if q["id"] == "h29-17")
    assert case["e2e_query"] == "预约签到有效状态的行数，列n"
    assert len(value["e2e_case_ids"]) == 11
    raw = json.dumps(value)
    assert "api_key" not in raw and "localhost" not in raw
