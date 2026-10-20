"""Opt-in LangSmith export. Only newly constructed, bounded metadata leaves the app."""

import logging
from datetime import UTC, datetime
from queue import Full, Queue
from threading import Lock, Thread
from uuid import uuid4

from langsmith import Client
from urllib3.util.retry import Retry

from app.observability import emit

NODES = {"prepare", "retrieve", "plan", "generate", "validate", "repair", "execute", "check_result"}
TOOLS = {
    "inspect_dataset_schema",
    "get_profile_summary",
    "search_business_knowledge",
    "get_metric_definition",
    "request_clarification",
}
STATUSES = {"success", "error", "rejected", "clarification"}
RAG_STATUSES = {
    "not_requested",
    "not_configured",
    "no_documents",
    "space_mismatch",
    "retrieved",
    "no_match",
    "error",
}
COUNTS = {
    "duration_ms",
    "repair_attempts",
    "sql_model_calls",
    "agent_model_calls",
    "agent_tool_calls",
    "retrieved_count",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
}


def safe_metadata(values):
    """No arbitrary strings, identifiers, nested payloads or exception messages."""
    safe = {k: v for k, v in values.items() if k in COUNTS and type(v) is int and 0 <= v <= 10**9}
    for key, allowed in (("status", STATUSES), ("rag_status", RAG_STATUSES)):
        value = values.get(key)
        if isinstance(value, str) and value in allowed:
            safe[key] = value
    if type(values.get("rag_used")) is bool:
        safe["rag_used"] = values["rag_used"]
    return safe


class SafeSDKLogs(logging.Filter):
    def filter(self, record):
        # SDK failures may include remote response bodies. Keep these out of logs.
        record.msg, record.args, record.exc_info, record.exc_text = (
            "LangSmith metadata transport event (details suppressed)",
            (),
            None,
            None,
        )
        return True


logging.getLogger("langsmith.client").addFilter(SafeSDKLogs())


def client_for(settings, on_error):
    return Client(
        api_urls={settings.langsmith_endpoint: settings.langsmith_api_key},
        auto_batch_tracing=False,
        timeout_ms=(500, 1000),
        retry_config=Retry(total=0),
        hide_inputs=lambda _: {},
        hide_outputs=lambda _: {},
        hide_metadata=safe_metadata,
        omit_traced_runtime_info=True,  # Also suppress SDK-added environment/git metadata.
        tracing_error_callback=on_error,
        info={"batch_ingest_config": {"size_limit_bytes": 1_000_000}},
        tracing_sampling_rate=1.0,
        tracing_mode="langsmith",
    )


def trace_payload(run, status, duration_ms):
    """Build a fresh snapshot; graph state and business telemetry are never serialized."""
    root_id = uuid4()
    start = run.started_at
    order = start.strftime("%Y%m%dT%H%M%S%fZ") + str(root_id)
    metadata = safe_metadata(
        {
            "status": status,
            "duration_ms": duration_ms,
            "repair_attempts": run.repairs,
            "sql_model_calls": run.sql_calls,
            "agent_model_calls": run.agent_metrics.get("model_calls"),
            "agent_tool_calls": run.agent_metrics.get("tool_calls"),
            "rag_status": run.rag.get("status"),
            "rag_used": run.rag.get("used"),
            "retrieved_count": run.rag.get("retrieved_count", len(run.rag.get("citations", []))),
            **run.usage().model_dump(exclude={"source"}),
        }
    )
    rows = [
        {
            "id": root_id,
            "trace_id": root_id,
            "dotted_order": order,
            "name": "datapilot.analysis",
            "run_type": "chain",
            "start_time": start,
            "end_time": datetime.now(UTC),
            "inputs": {},
            "outputs": {},
            "extra": {"metadata": metadata},
            "error": "analysis_failed" if status in {"error", "rejected"} else None,
        }
    ]
    parent_orders = {}
    for span in run.node_spans[:12] + run.agent_spans[:10]:
        name = span.get("name")
        if name not in NODES | TOOLS | {"agent_model"}:
            continue
        child_id = uuid4()
        parent_id, parent_order = (
            parent_orders.get("plan", (root_id, order))
            if name in TOOLS | {"agent_model"}
            else (root_id, order)
        )
        child_order = (
            parent_order + "." + span["start"].strftime("%Y%m%dT%H%M%S%fZ") + str(child_id)
        )
        rows.append(
            {
                "id": child_id,
                "trace_id": root_id,
                "parent_run_id": parent_id,
                "dotted_order": child_order,
                "name": name,
                "run_type": "tool"
                if name in TOOLS
                else "llm"
                if name in {"generate", "repair", "agent_model"}
                else "retriever"
                if name == "retrieve"
                else "chain",
                "start_time": span["start"],
                "end_time": span["end"],
                "inputs": {},
                "outputs": {},
                "extra": {"metadata": safe_metadata({"status": span["status"]})},
                "error": "stage_failed" if span["status"] == "error" else None,
            }
        )
        if name == "plan":
            parent_orders[name] = (child_id, child_order)
    return rows


def deliver(settings, rows):
    errors = []
    client = None
    status = "error"
    try:
        client = client_for(settings, lambda _: errors.append(True))
        for row in rows:
            row["session_name"] = settings.langsmith_project
        client.batch_ingest_runs(create=rows)
        status = "error" if errors else "success"
    except Exception:
        status = "error"
    finally:
        if client is not None:
            try:
                client.close(timeout=0)
            except Exception:
                status = "error"
    emit("langsmith_export", status=status)


_queue = Queue(maxsize=8)
_lock = Lock()
_started = False


def _worker():
    while True:
        settings, rows = _queue.get()
        try:
            deliver(settings, rows)
        finally:
            _queue.task_done()


def export_trace(settings, run, status, duration_ms):
    if not settings.datapilot_langsmith_enabled or not settings.langsmith_api_key:
        return "disabled"
    try:
        return _enqueue(settings, run, status, duration_ms)
    except Exception:
        emit("langsmith_export", status="error")
        return "error"


def _enqueue(settings, run, status, duration_ms):
    global _started
    with _lock:
        if not _started:
            Thread(target=_worker, name="datapilot-metadata-tracing", daemon=True).start()
            _started = True
    try:
        _queue.put_nowait((settings.model_copy(), trace_payload(run, status, duration_ms)))
        return "queued"
    except Full:
        emit("langsmith_export", status="dropped")
        return "dropped"
