"""Opt-in LangSmith export. Only newly constructed, bounded metadata leaves the app."""

import hashlib
import json
import logging
import re
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
    "agent_total_tokens",
}
LABELS = {
    "provider": {"deepseek", "openai", "openai_compatible", "mock", "custom"},
    "model": {"deepseek-flash", "deepseek-chat", "gpt-4o-mini", "mock", "custom"},
    "embedding_model": {"text-embedding-v4", "text-embedding-3-small", "fake", "custom"},
    "retrieval_strategy": {"vector", "hybrid"},
    "reranker": {"none", "flashrank"},
    "sql_validation": {"approved", "rejected", "not_requested"},
    "failure_code": {
        "unsafe_sql",
        "unknown_column",
        "query_timeout",
        "llm_timeout",
        "agent_budget_exceeded",
        "reranker_timeout",
        "reranker_failed",
        "contract_failed",
        "other",
    },
}


def safe_metadata(values):
    """Only enumerated labels/counts and per-trace salted source hashes; no bodies."""
    safe = {k: v for k, v in values.items() if k in COUNTS and type(v) is int and 0 <= v <= 10**9}
    for key, allowed in (("status", STATUSES), ("rag_status", RAG_STATUSES)):
        value = values.get(key)
        if isinstance(value, str) and value in allowed:
            safe[key] = value
    if type(values.get("rag_used")) is bool:
        safe["rag_used"] = values["rag_used"]
    if type(values.get("contract_passed")) is bool:
        safe["contract_passed"] = values["contract_passed"]
    version = values.get("retrieval_config_hash")
    if isinstance(version, str) and re.fullmatch(r"[0-9a-f]{16}", version):
        safe["retrieval_config_hash"] = version
    candidates = values.get("candidates")
    if isinstance(candidates, list) and len(candidates) <= 80:
        clean = []
        for row in candidates:
            if not isinstance(row, dict) or set(row) != {
                "stage",
                "source_hash",
                "rank",
                "score_ppm",
            }:
                continue
            if (
                row["stage"] in {"vector", "lexical", "fused", "reranked", "selected"}
                and isinstance(row["source_hash"], str)
                and re.fullmatch(r"[0-9a-f]{16}", row["source_hash"])
                and type(row["rank"]) is int
                and 1 <= row["rank"] <= 16
                and type(row["score_ppm"]) is int
                and 0 <= row["score_ppm"] <= 10**6
            ):
                clean.append(row)
        safe["candidates"] = clean
    for key, allowed in LABELS.items():
        if isinstance(values.get(key), str) and values[key] in allowed:
            safe[key] = values[key]
    hashes = values.get("source_hashes")
    if (
        isinstance(hashes, list)
        and len(hashes) <= 8
        and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{16}", value) for value in hashes)
    ):
        safe["source_hashes"] = hashes
    return safe


def retrieval_metadata(settings, rag, salt):
    """No text or stable source identifiers cross the tracing boundary."""
    config = {
        k: getattr(settings, k)
        for k in (
            "rag_strategy",
            "rag_reranker",
            "rag_top_k",
            "rag_min_similarity",
            "reranker_min_score",
        )
    }
    candidates = []
    for stage in rag.get("candidate_stages", [])[:5]:
        for rank, row in enumerate(stage.get("candidates", [])[:16], 1):
            key = row.get("chunk_id")
            score = row.get("reranker_score", row.get("fusion_score", row.get("similarity", 0)))
            if isinstance(key, str) and type(score) in (int, float) and 0 <= score <= 1:
                candidates.append(
                    {
                        "stage": stage.get("stage"),
                        "rank": rank,
                        "source_hash": hashlib.sha256((str(salt) + "|" + key).encode()).hexdigest()[
                            :16
                        ],
                        "score_ppm": round(score * 10**6),
                    }
                )
    return safe_metadata(
        {
            "retrieval_config_hash": hashlib.sha256(
                json.dumps(config, sort_keys=True).encode()
            ).hexdigest()[:16],
            "candidates": candidates,
        }
    )


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


def client_for(settings, on_error, *, timeout_ms=(5000, 5000)):
    return Client(
        api_urls={settings.langsmith_endpoint: settings.langsmith_api_key},
        auto_batch_tracing=False,
        timeout_ms=timeout_ms,
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
            "provider": run.settings.llm_provider
            if run.settings.llm_provider in LABELS["provider"]
            else "custom",
            "model": "mock"
            if run.settings.llm_provider == "mock"
            else run.settings.llm_model
            if run.settings.llm_model in LABELS["model"]
            else "custom",
            "embedding_model": "fake"
            if run.settings.embedding_provider == "fake"
            else run.settings.embedding_model
            if run.settings.embedding_model in LABELS["embedding_model"]
            else "custom",
            "retrieval_strategy": run.settings.rag_strategy,
            "reranker": run.settings.rag_reranker,
            "agent_total_tokens": run.agent_metrics.get("usage", {}).get("total_tokens"),
            "sql_validation": run.validations[-1]["status"] if run.validations else "not_requested",
            "source_hashes": [
                hashlib.sha256((str(root_id) + "|" + source["chunk_id"]).encode()).hexdigest()[:16]
                for source in run.rag.get("citations", [])[:8]
                if isinstance(source.get("chunk_id"), str)
            ],
            **run.usage().model_dump(exclude={"source"}),
            **retrieval_metadata(run.settings, run.rag, root_id),
            "failure_code": (
                run.failure.detail.get("code")
                if run.failure and run.failure.detail.get("code") in LABELS["failure_code"]
                else "other"
                if run.failure
                else None
            ),
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
                "extra": {
                    "metadata": safe_metadata(
                        {
                            "status": span["status"],
                            "duration_ms": max(
                                0, int((span["end"] - span["start"]).total_seconds() * 1000)
                            ),
                        }
                    )
                },
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
    emit("langsmith_export", status=status, local_trace_id=str(rows[0]["id"]) if rows else None)


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
