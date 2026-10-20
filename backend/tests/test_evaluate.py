import os

import pytest

from app.evaluate import case_evidence, evaluate, load_cases, same_rows, score_case, summarize


def test_fixed_cases_cover_required_categories_and_result_comparison():
    cases = load_cases()
    assert len(cases) == 29 and len({c["id"] for c in cases}) == 29
    assert {
        "alias",
        "metric",
        "missing-definition",
        "no-hit",
        "knowledge-injection",
        "cross-owner",
        "llm-timeout",
        "bad-json",
        "query-error",
    }.issubset({c["id"] for c in cases})
    assert same_rows([{"x": 1.0, "y": None}], [{"x": 1, "y": None}])
    assert not same_rows([], [{"x": 1}])
    assert not same_rows([{"wrong_name": 1}], [{"x": 1}])
    assert not same_rows([{"x": True}], [{"x": 1}])
    assert not same_rows([{"x": float("nan")}], [{"x": 1}])


def test_missing_results_unknown_usage_and_denominators_are_not_fabricated():
    case = {"id": "answer", "rows": [{"metric": 3}]}
    bad = score_case(case, {"status": "error", "error_code": "unsafe_sql"})
    assert not bad["correct"] and bad["false_refusal"] and bad["tokens"] is None
    assert score_case({"id": "empty", "rows": []}, {"status": "success", "result": {"rows": []}})[
        "correct"
    ]
    bad["latency_ms"] = 10
    summary = summarize([bad])
    assert summary["query_result_accuracy"] == 0 and summary["false_refusal_rate"] == 1
    assert summary["tokens"] is None and summary["cost_currency"] is None
    assert summary["safety_pass_rate"] is None and summary["retrieval_recall_at_k"] is None
    assert summarize([])["query_result_accuracy"] is None


def test_safety_requires_no_execution_and_recall_uses_registered_ground_truth():
    case = {
        "id": "unsafe",
        "safety": True,
        "error": "unsafe_sql",
        "expected_titles": ["one", "two"],
    }
    response = {"error_code": "unsafe_sql", "telemetry": {"workflow": {"nodes": ["execute"]}}}
    scored = score_case(
        case, response, retrieval={"citations": [{"title": "one"}, {"title": "invented"}]}
    )
    assert scored["correct"] and not scored["safety_ok"] and scored["recall_at_k"] == 0.5


def test_real_evaluation_requires_two_opt_ins_before_database_access(monkeypatch):
    monkeypatch.delenv("RUN_LIVE_EVAL", raising=False)
    with pytest.raises(ValueError, match="RUN_LIVE_EVAL"):
        evaluate("postgresql://unused", live=True)
    with pytest.raises(ValueError, match="PostgreSQL"):
        evaluate("sqlite://")


def test_synthetic_evidence_excludes_credentials_raw_errors_and_unrelated_fields():
    evidence = case_evidence(
        {
            "sql": "SELECT COUNT(*) AS metric FROM data",
            "result": {"rows": [{"metric": 4}]},
            "error_message": "private-error-body",
            "api_key": "private-placeholder",
            "telemetry": {"workflow": {"nodes": ["validate", "execute"]}},
        },
        {"citations": [{"title": "sales", "similarity": 0.7, "excerpt": "private-content"}]},
    )
    assert evidence["rows"] == [{"metric": 4}]
    assert evidence["nodes"] == ["validate", "execute"]
    assert evidence["citations"] == [{"title": "sales", "similarity": 0.7}]
    assert "private" not in str(evidence)


@pytest.mark.postgres
def test_offline_fixed_evaluation_runs_real_api_pgvector_and_duckdb():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("Fixed end-to-end evaluation requires independent PostgreSQL test DB")
    completed = []
    report = evaluate(url, on_record=lambda mode, row: completed.append((mode, row)))
    assert len(completed) == 58
    assert completed[-1] == ("enhanced", report["records"]["enhanced"][-1])
    assert report["providers"]["langsmith_enabled"] is False
    assert {"llm-timeout", "bad-json", "query-timeout", "oversized-result"}.issubset(
        report["fault_injection"]
    )
    assert report["mode"] == "controlled-offline-not-model-accuracy"
    assert all(r["correct"] for r in report["records"]["enhanced"])
    assert all(r["safety_ok"] for r in report["records"]["enhanced"] if r["safety_ok"] is not None)
    assert report["summary"]["enhanced"]["retrieval_recall_at_k"] == 1
    assert report["summary"]["enhanced"]["query_result_accuracy"] == 1
    assert report["repair_comparison"] == {
        "baseline": {"cases": 1, "correct": 0},
        "enhanced": {"cases": 1, "correct": 1},
    }
    assert report["summary"]["baseline"]["query_result_accuracy"] < 1
