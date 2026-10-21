"""Read actual service traces and correlate local deterministic assessments."""

import hashlib
import json
import os
import time
from datetime import datetime

from app.config import ROOT, Settings
from app.langsmith_acceptance import assert_private_trace
from app.rag18_evaluate import write
from app.tracing import client_for


def service_events(path):
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            row = json.loads(line[start:])
        except ValueError:
            continue
        if row.get("event") in {"analysis_completed", "langsmith_export"}:
            events.append(row)
    return events


def matches(saved, completed):
    metadata = saved.extra.get("metadata", {})
    duration = completed["duration_ms"]
    delta = (datetime.fromisoformat(completed["timestamp"]) - saved.start_time).total_seconds()
    workflow = completed["workflow"]
    return (
        metadata.get("duration_ms") == duration
        and metadata.get("status") == completed["status"]
        and metadata.get("agent_model_calls") == workflow["agent"]["model_calls"]
        and metadata.get("sql_model_calls") == workflow["sql_model_calls"]
        and abs(delta - duration / 1000) < 2
    )


def run():
    if os.environ.get("RUN_LIVE_LANGSMITH") != "1":
        raise ValueError("Explicit cloud readback opt-in required")
    settings = Settings().model_copy(update={"datapilot_langsmith_enabled": True})
    events = service_events(ROOT / "data/runtime/phase19-service-live.log")
    completed = [e for e in events if e["event"] == "analysis_completed"]
    identifiers = [
        e["local_trace_id"]
        for e in events
        if e["event"] == "langsmith_export" and e.get("status") == "success"
    ]
    browser = json.loads((ROOT / "data/runtime/phase19-live-browser.json").read_bytes())
    contracts = {r["analysis_id"]: r["contract_passed"] for r in browser["records"]}
    workflows = {r["analysis_id"]: r["telemetry"]["workflow"] for r in browser["records"]}
    # Separate diagnostic is identified by its exact question, never by assumed log order.
    diagnostic = [e for e in completed if e["analysis_id"] not in contracts]
    if len(diagnostic) != 1 or len(completed) != 6 or len(identifiers) != 6:
        raise ValueError("Unexpected actual trace cardinality")
    contracts[diagnostic[0]["analysis_id"]] = True
    diagnostic_browser = json.loads(
        (ROOT / "data/runtime/phase19-exact-name-diagnostic-real-browser.json").read_bytes()
    )
    if diagnostic_browser["status"] != "passed":
        raise ValueError("Diagnostic browser evidence missing")
    workflows[diagnostic[0]["analysis_id"]] = diagnostic_browser["telemetry"]["workflow"]
    for event in completed:
        event["workflow"] = workflows[event["analysis_id"]]
    errors, results = [], []
    report = {
        "status": "in_progress",
        "actual_service_runs": results,
        "project": settings.langsmith_project,
        "raw_content_exported": False,
    }
    output = ROOT / "docs/phase19-cloud-readback-v2.json"
    write(output, report, exclusive=True)
    client = client_for(settings, lambda _: errors.append(True), timeout_ms=(5000, 10000))
    try:
        for index, identifier in enumerate(identifiers):
            if index:
                time.sleep(15)
            saved = client.read_run(identifier, load_child_runs=True)
            found = [event for event in completed if matches(saved, event)]
            if len(found) != 1:
                raise ValueError("Trace association is ambiguous")
            event = found[0]
            workflow = event["workflow"]
            expected = {"datapilot.analysis", *workflow["nodes"], *workflow["agent"]["tools_used"]}
            if workflow["agent"]["model_calls"]:
                expected.add("agent_model")
            names = assert_private_trace(saved, expected)
            metadata = dict(saved.extra["metadata"])
            metadata.pop("ls_run_depth", None)
            expected_sources = [
                hashlib.sha256((str(saved.id) + "|" + c["chunk_id"]).encode()).hexdigest()[:16]
                for c in workflow["rag"]["citations"][:8]
            ]
            if metadata.get("source_hashes") != expected_sources:
                raise ValueError("Cloud adopted source correlation mismatch")
            # Evaluation belongs to the local receipt. Do not patch a measured run:
            # SDK update_run defaults end_time to now and may buffer updates.
            actual_ms = (saved.end_time - saved.start_time).total_seconds() * 1000
            if abs(actual_ms - event["duration_ms"]) > 1000:
                raise ValueError("Cloud execution timing does not match measured service duration")
            results.append(
                {
                    "analysis_id": event["analysis_id"],
                    "run_id": str(saved.id),
                    "url": client.get_run_url(run=saved),
                    "names": names,
                    "association": "Unique duration/status/model counts and actual time interval",
                    "metadata": saved.extra["metadata"],
                    "local_contract_passed": contracts[event["analysis_id"]],
                    "contract_annotation": "Local assessment; cloud execution record unchanged",
                    "inputs_outputs_empty": True,
                }
            )
            write(output, report)
        if errors:
            raise ValueError("Cloud callback failure")
        report["status"] = "server-readback-confirmed"
    except Exception as error:
        report["status"] = "blocked"
        report["failure_class"] = type(error).__name__
        # Exception bodies can contain URLs: persist only fixed categorical hints.
        report["rate_hints"] = [
            term
            for term in ("minute", "hour", "month", "quota", "limit")
            if term in str(error).casefold()
        ]
        raise
    finally:
        write(output, report)
        client.close(timeout=0)
    return report


if __name__ == "__main__":
    try:
        print(json.dumps({"status": run()["status"]}))
    except Exception as error:
        print(json.dumps({"status": "failed", "failure_class": type(error).__name__}))
        raise SystemExit(1) from None
