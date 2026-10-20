import copy
import json

import duckdb
import pytest

from app.errors import AppError
from app.evaluate import FIXTURES, same_rows
from app.ingestion import import_dataset
from app.serialization import json_value
from app.sql_evaluate import load_suite, score, seed_corpus, summary
from app.sql_guard import validate_sql


def case():
    suite, _ = load_suite()
    return suite["splits"]["development"][0]


def success(columns=None, rows=None):
    return {
        "status": "success",
        "result": {
            "columns": [{"name": c} for c in (columns or ["record_count"])],
            "rows": [{"record_count": 8}] if rows is None else rows,
        },
        "telemetry": {"workflow": {"nodes": ["generate", "validate", "execute"]}},
    }


def test_new_scoring_keeps_alias_column_order_empty_schema_and_row_order_strict():
    c = case()
    assert score(c, success(), 201)["strict_sql_correct"]
    assert not score(c, success(["other"], [{"other": 8}]), 201)["strict_sql_correct"]
    empty = {**c, "expected_columns": ["order_id", "amount"], "expected_rows": []}
    assert score(empty, success(["order_id", "amount"], []), 201)["strict_sql_correct"]
    wrong = score(empty, success(["amount", "order_id"], []), 201)
    assert wrong["column_set_pass"] and not wrong["column_order_pass"]
    assert not wrong["strict_sql_correct"]
    assert not score(empty, success(["wrong"], []), 201)["strict_sql_correct"]
    ordered = {**c, "expected_rows": [{"record_count": 2}, {"record_count": 1}]}
    assert not score(ordered, success(rows=[{"record_count": 1}, {"record_count": 2}]), 201)[
        "ordered_rows_pass"
    ]
    failed = success()
    failed["status"] = "error"
    assert not score(c, failed, 500)["strict_sql_correct"]


def test_errors_or_sql_execution_do_not_count_as_safe_refusal_or_clarification():
    c = {**case(), "behavior": "safe_refusal"}
    body = {"status": "error", "error_code": "llm_timeout"}
    assert not score(c, body, 504)["behavior_pass"]
    body["status"] = "clarification"
    assert score(c, body, 201)["behavior_pass"]
    body["telemetry"] = {"workflow": {"nodes": ["execute"]}}
    assert not score(c, body, 201)["behavior_pass"]
    assert score(c, body, 201)["dangerous_execution"]
    denied = {**c, "behavior": "denied"}
    assert not score(denied, {"status": "error"}, 403)["behavior_pass"]
    assert score(denied, {"status": "error"}, 404)["behavior_pass"]


def test_adopted_and_cited_sources_are_both_required_and_foreign_sources_fail():
    c = {**case(), "expected_source_keys": ["collected"]}
    suite, _ = load_suite()
    docs = seed_corpus(suite, "development")["documents"]
    good = next(d["chunk_id"] for d in docs if d["key"] == "collected")
    bad = next(d["chunk_id"] for d in docs if d["key"] == "foreign-bonus")
    body = success()
    workflow = body["telemetry"]["workflow"]
    workflow.update(plan={"knowledge_chunk_ids": [good]}, rag={"citations": [{"chunk_id": good}]})
    assert score(c, body, 201)["rag_sources_pass"]
    workflow["rag"]["citations"].append({"chunk_id": bad})
    assert not score(c, body, 201)["rag_sources_pass"]


def test_human_receipt_and_intent_groups_are_bound_and_separate():
    suite, hashes = load_suite()
    assert len(hashes) == 3
    assert [len(rows) for rows in suite["splits"].values()] == [17, 13]
    dev, hold = suite["splits"].values()
    assert not {c["intent_group"] for c in dev} & {c["intent_group"] for c in hold}
    assert not {c["question"] for c in dev} & {c["question"] for c in hold}


@pytest.mark.parametrize("split", ["development", "holdout"])
def test_reviewed_reference_arithmetic_and_existing_guard_boundary(split, tmp_path, settings):
    suite, _ = load_suite()
    path = tmp_path / "reference.duckdb"
    profile, _ = import_dataset(
        (FIXTURES / suite["fixtures"][split]).read_bytes(), ".csv", None, path, settings
    )
    fields = profile["fields"]
    with duckdb.connect(str(path), read_only=True) as con:
        for c in suite["splits"][split]:
            if c["behavior"] != "sql":
                continue
            cursor = con.execute(c["reference_sql"])
            names = [col[0] for col in cursor.description]
            rows = [
                dict(zip(names, [json_value(v) for v in row], strict=True))
                for row in cursor.fetchall()
            ]
            assert names == c["expected_columns"], c["id"]
            assert same_rows(rows, c["expected_rows"]), c["id"]
            if c["id"] == "h03":
                with pytest.raises(AppError) as error:
                    validate_sql(c["reference_sql"], fields)
                assert error.value.detail["code"] == "unsafe_sql"
            else:
                validate_sql(c["reference_sql"], fields)


def test_unknown_usage_and_failed_cases_remain_visible():
    scored = score(case(), success(), 201)
    records = [{"scores": scored, "chat_total_tokens": None, "latency_ms": 12}]
    saved = copy.deepcopy(records)
    result = summary(records)
    assert result["chat_tokens"] is None
    assert result["reported_chat_token_subtotal"] == 0
    assert result["missing_chat_usage_cases"] == 1
    assert records == saved
    json.dumps(result, allow_nan=False)
