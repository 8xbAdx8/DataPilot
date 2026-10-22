"""Offline audit of immutable real RAG24 artifacts. No service or DB calls.

Recomputes label metrics and baseline cosine candidates from published vectors.
This verifies the existing run; it never launches another official holdout.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
from app.rag24_evaluate import HOLDOUT_FREEZE, summarize, verify_freeze
from app.rag24_judge import judge

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs/rag24"


def read(path):
    return json.loads(path.read_bytes())


def verify():
    receipt = read(ARTIFACTS / "real-vector-cache-v1.json")
    path = ARTIFACTS / receipt["artifact"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["sha256"]
    with np.load(path, allow_pickle=False) as stored:
        assert set(stored.files) == {"input_sha256", "vectors"}
        assert stored["vectors"].shape == (122, 1536)
        vectors = dict(zip(stored["input_sha256"], stored["vectors"], strict=True))
    freeze = read(HOLDOUT_FREEZE)
    verify_freeze(freeze)
    corpus = read(ROOT / "backend/app/evals/rag24/holdout-v1.json")
    assert (
        hashlib.sha256(
            (ROOT / "backend/app/evals/rag24/holdout-v1.json").read_bytes()
        ).hexdigest()
        == freeze["corpus_sha256"]
    )
    records = read(ARTIFACTS / "holdout-02/records.json")
    summary = read(ARTIFACTS / "holdout-02/summary.json")
    assert len(records) == 34 and len({r["id"] for r in records}) == 34
    for strategy in ("A", "B", "C"):
        assert summarize(corpus, records, strategy) == summary["strategies"][strategy]

    def vector(text):
        # pgvector's physical column uses float32; normalize for cosine.
        value = (
            vectors[hashlib.sha256(text.encode()).hexdigest()]
            .astype(np.float32)
            .astype(np.float64)
        )
        return value / np.linalg.norm(value)

    by_id = {r["id"]: r for r in records}
    cosine_verified = 0
    for query in corpus["queries"]:
        qvec = vector(query["query"])
        allowed = [
            d
            for d in corpus["documents"]
            if d["owner"] == query["owner"] and d["scope"] in {"target", "personal"}
        ]
        ranked = sorted(
            [(float(np.dot(qvec, vector(d["content"]))), d["id"]) for d in allowed],
            reverse=True,
        )
        nearest = [name for score, name in ranked if score >= 0.70][:4]
        actual = by_id[query["id"]]["A"]
        assert nearest == actual["ids"], query["id"]
        scores = {name: score for score, name in ranked}
        for name, source in zip(actual["ids"], actual["sources"], strict=True):
            assert abs(scores[name] - source["similarity"]) < 2e-6
        cosine_verified += 1
    ledger = read(ARTIFACTS / "complete-ledger-v1.json")
    assert len({r["id"] for r in ledger["requests"]}) == 13
    input_hashes = [h for r in ledger["requests"] for h in r["input_hashes"]]
    assert len(input_hashes) == len(set(input_hashes)) == len(vectors) == 122
    assert set(input_hashes) == set(vectors)
    assert sum(r["usage"]["prompt_tokens"] for r in ledger["requests"]) == 2934
    criteria = read(ROOT / "backend/app/evals/rag24/acceptance-v1.json")
    verdict = judge(
        summary["strategies"],
        ledger,
        criteria,
        references_persisted=summary["cross_connection_persistence_verified"],
        cleanup_ok=summary["cleanup"]["database_removed"]
        and summary["base_database_counts_unchanged"],
    )
    saved = read(ARTIFACTS / "holdout-02/verdict.json")
    assert verdict["status"] == saved["status"] == "PASS"
    assert verdict["checks"] == saved["checks"]
    assert (
        saved["raw_summary_sha256"]
        == hashlib.sha256(
            (ARTIFACTS / "holdout-02/summary.json").read_bytes()
        ).hexdigest()
    )
    return {
        "status": "verified",
        "kind": "offline_audit_of_existing_real_run_not_new_evaluation",
        "real_vectors": len(vectors),
        "dimensions": 1536,
        "baseline_cosine_cases": cosine_verified,
        "label_metric_strategies": 3,
        "actual_embedding_requests": 13,
        "actual_input_tokens": 2934,
        "new_service_calls": 0,
    }


if __name__ == "__main__":
    print(json.dumps(verify()))
