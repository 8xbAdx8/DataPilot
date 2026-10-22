"""Offline re-audit of the single frozen real run; never reruns retrieval/models."""

import json
import subprocess

import duckdb
from app.config import ROOT
from app.phase27_acceptance import score
from app.phase29_acceptance import CONFIGS, FIXTURES, OUTPUT, corpus, manifest
from app.sql_guard import validate_sql

value = corpus()
frozen = json.loads((ROOT / "docs/phase29/freeze-v1.json").read_bytes())
current = manifest()
# The freeze records the pre-commit HEAD plus exact new source hashes. A final
# documentation/evidence commit must preserve those bytes, not falsify its HEAD.
assert (
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", frozen["head"], "HEAD"], check=False
    ).returncode
    == 0
)
assert {k: v for k, v in frozen.items() if k != "head"} == {
    k: v for k, v in current.items() if k != "head"
}
rows = [
    json.loads((OUTPUT / (q["id"] + ".json")).read_bytes()) for q in value["queries"]
]
scores = score(rows, CONFIGS)
assert scores == json.loads((OUTPUT / "retrieval-scores.json").read_bytes())
summary = json.loads((OUTPUT / "summary.json").read_bytes())
assert summary["scores"] == scores and summary["status"] == "PARTIAL"
ledger = summary["ledger"]
assert ledger["usage_complete"] and ledger["stopped"] is None
requests = [r for r in ledger["requests"] if r["stage"] == 29]
assert all(r["status"] == "complete" and r["provider_id"] for r in requests)
api = [
    json.loads((OUTPUT / (case + "-api.json")).read_bytes())
    for case in value["e2e_case_ids"]
]
assert summary["api_correct"] == sum(r["correct"] for r in api)
model_calls = [call for r in api for call in r["model_http"]]
assert len(model_calls) == sum(r["kind"] == "chat" for r in requests)
assert sum(c["usage"]["prompt_tokens"] for c in model_calls) == sum(
    r["input_tokens"] for r in requests if r["kind"] == "chat"
)
native_plans = [c for c in model_calls if c["channel"] == "agent"]
assert all(
    any(
        t["function"]["name"] == "AnalysisPlan"
        for t in c["choices"][0]["message"]["tool_calls"]
    )
    for c in native_plans
)
checked = 0
with duckdb.connect() as connection:
    connection.execute(
        "CREATE TABLE data AS SELECT * FROM read_csv_auto(?)",
        [str(FIXTURES / "bookings.csv")],
    )
    fields = [
        {"name": r[1], "type": r[2]}
        for r in connection.execute("PRAGMA table_info('data')").fetchall()
    ]
    for record in api:
        for execution in record["executions"]:
            sql = validate_sql(execution["sql"], fields)
            result = connection.execute(sql)
            columns = [c[0] for c in result.description]
            actual = [dict(zip(columns, r, strict=True)) for r in result.fetchall()]
            assert actual == execution["result"]["rows"]
            checked += 1
        if record["response"].get("status") == "success":
            assert record["history"]["result"] == record["response"]["result"]
            assert "analysis_succeeded" in record["audit_events"]
        elif record["response"].get("status") == "clarification":
            assert not record["executions"]
            assert "analysis_clarification" in record["audit_events"]
assert summary["cleanup"]["database_removed"]
print(
    json.dumps(
        {
            "status": "verified",
            "kind": "offline_audit_not_new_holdout_run",
            "queries": len(rows),
            "native_model_plan_responses": len(native_plans),
            "guarded_sql_replays": checked,
            "chat_http": len(model_calls),
            "embedding_http": sum(r["kind"] == "embedding" for r in requests),
            "new_external_calls": 0,
            "original_score_preserved": True,
        }
    )
)
