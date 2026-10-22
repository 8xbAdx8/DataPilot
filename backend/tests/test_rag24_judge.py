from copy import deepcopy

import pytest

from app.rag24_evaluate import FIXTURES
from app.rag24_judge import judge


def fixtures():
    import json

    contract = json.loads((FIXTURES / "acceptance-v1.json").read_bytes())
    strategy = {
        kind: {"cases": n, "recall_at_k": 1, "correct_no_match": n}
        for kind, n in contract["expected_counts"].items()
    }
    strategy.update(unauthorized_leaks=0, reference_errors=0, malicious_sources_returned=0)
    ledger = {
        "http_requests_reserved": 1,
        "known_input_tokens": 10,
        "usage_complete": True,
        "stopped": None,
        "requests": [
            {
                "status": "complete",
                "provider_request_id": "synthetic",
                "actual": 10,
                "reserved": 50,
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            }
        ],
    }
    return contract, {"C": strategy}, ledger


def test_acceptance_is_retrieval_scoped_and_does_not_invent_sql_execution():
    contract, strategies, ledger = fixtures()
    result = judge(strategies, ledger, contract, references_persisted=True, cleanup_ok=True)
    assert result["status"] == "PASS" and result["sql_execution"] == "NOT_RUN"


@pytest.mark.parametrize(
    "fault",
    [
        "metric",
        "knowledge",
        "negative",
        "leak",
        "reference",
        "malicious",
        "usage",
        "empty",
        "persist",
        "cleanup",
    ],
)
def test_any_frozen_gate_failure_cannot_be_hidden_by_high_other_scores(fault):
    contract, strategies, original = fixtures()
    ledger = deepcopy(original)
    if fault in {"metric", "knowledge"}:
        strategies["C"][fault]["recall_at_k"] = 0.8
    elif fault == "negative":
        strategies["C"]["negative"]["correct_no_match"] = 8
    elif fault in {"leak", "reference", "malicious"}:
        strategies["C"][
            {
                "leak": "unauthorized_leaks",
                "reference": "reference_errors",
                "malicious": "malicious_sources_returned",
            }[fault]
        ] = 1
    elif fault == "usage":
        ledger["usage_complete"] = False
    elif fault == "empty":
        ledger["requests"] = []
    result = judge(
        strategies,
        ledger,
        contract,
        references_persisted=fault != "persist",
        cleanup_ok=fault != "cleanup",
    )
    assert result["status"] == "QUALITY_NOT_PASSED"
