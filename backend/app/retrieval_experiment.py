"""Default-off C integration; D remains ineligible. Never makes external requests."""

import hashlib
import time
from copy import deepcopy

from app.errors import AppError
from app.observability import emit
from app.rag24_policy import select_knowledge


def require_isolation(session, settings):
    url = session.get_bind().engine.url
    if not settings.rag_experiment_synthetic or not (
        url.get_backend_name() == "postgresql"
        and url.host in {"127.0.0.1", "localhost"}
        and (
            url.database.startswith(("rag24_", "datapilot_trial_"))
            or url.database == "datapilot_test"
        )
    ):
        raise AppError(503, "experiment_isolation_required", "实验仅限显式合成隔离测试库。")


def candidate(registry, question, citations):
    sources, decision = select_knowledge(registry, question, deepcopy(citations))
    return sources, decision


def apply_experiment(session, registry, question, settings, rag):
    if settings.rag_experiment_mode == "off":
        return rag
    require_isolation(session, settings)
    start = time.perf_counter()
    old = rag.get("citations", [])
    selected, decision = candidate(registry, question, old)

    # Counts/hashes only; no query, body, SQL, tool arguments, owner or dataset.
    def fingerprint(sources):
        return sorted(hashlib.sha256(s["chunk_id"].encode()).hexdigest()[:16] for s in sources)

    metadata = {
        "mode": settings.rag_experiment_mode,
        "candidate": "C_registered_and_structured_facts",
        "D": "disabled_development_gate_not_met",
        "baseline_count": len(old),
        "candidate_count": len(selected),
        "baseline_source_hashes": fingerprint(old),
        "candidate_source_hashes": fingerprint(selected),
        "changed": {s["chunk_id"] for s in old} != {s["chunk_id"] for s in selected},
        "candidate_decision": decision["status"],
        "latency_ms": round(1000 * (time.perf_counter() - start), 3),
        "extra_embedding_http": 0,
    }
    emit("retrieval_experiment", **metadata)
    result = {**rag, "experiment": metadata}
    if settings.rag_experiment_mode == "enabled":
        result.update(citations=selected, status="retrieved" if selected else "no_match")
        result["experiment_decision"] = decision
    return result
