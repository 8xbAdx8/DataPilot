"""Send only metadata from one measured retrieval failure, with server readback."""

import json
import os
from datetime import datetime
from uuid import uuid4

from app.config import ROOT, Settings
from app.langsmith_acceptance import confirm_trace
from app.rag18_evaluate import reviewed, write
from app.tracing import retrieval_metadata, safe_metadata


def run():
    if os.environ.get("RUN_LIVE_LANGSMITH") != "1":
        raise ValueError("Explicit cloud opt-in required")
    settings = Settings().model_copy(update={"datapilot_langsmith_enabled": True})
    corpus, _ = reviewed()
    report = json.loads((ROOT / "docs/rag18-development-v1.json").read_bytes())
    profile = next(p for p in report["profiles"] if p["configuration"] == report["selected"])
    labels = {c["id"]: c for c in corpus["queries"]}
    failed = next(
        r for r in profile["records"] if labels[r["id"]]["should_no_match"] and r["retrieved_ids"]
    )
    settings = settings.model_copy(update=profile["configuration"])
    run_id = uuid4()
    start = datetime.fromisoformat(failed["started_at"])
    metadata = safe_metadata(
        {
            **retrieval_metadata(settings, failed, run_id),
            "status": "error",
            "contract_passed": False,
            "failure_code": "contract_failed",
            "rag_status": failed["retrieval_status"],
            "rag_used": False,
            "retrieved_count": len(failed["retrieved_ids"]),
            "duration_ms": round(failed["retrieval_ms"]),
            "embedding_model": settings.embedding_model,
            "retrieval_strategy": settings.rag_strategy,
            "reranker": settings.rag_reranker,
        }
    )
    rows = [
        {
            "id": run_id,
            "trace_id": run_id,
            "name": "datapilot.rag-evaluation",
            "run_type": "retriever",
            "start_time": start,
            "end_time": datetime.fromisoformat(failed["ended_at"]),
            "dotted_order": start.strftime("%Y%m%dT%H%M%S%fZ") + str(run_id),
            "inputs": {},
            "outputs": {},
            "extra": {"metadata": metadata},
            "error": "retrieval_contract_failed",
        }
    ]
    result = confirm_trace(settings, rows)
    result.update(
        sample_id=failed["id"],
        provenance="Actual measured dev retrieval; no model call",
        failure="Expected no_match but retrieved authorized knowledge",
    )
    write(ROOT / "docs/rag18-cloud-readback-v1.json", result, exclusive=True)
    return result


if __name__ == "__main__":
    try:
        print(json.dumps({"status": run()["status"]}))
    except Exception as error:
        print(json.dumps({"status": "failed", "failure_class": type(error).__name__}))
        raise SystemExit(1) from None
