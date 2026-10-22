import copy

import pytest

from app.rag_evaluate import load_corpus
from app.ragas_evaluate import samples


def test_optional_adapter_uses_reviewed_synthetic_context_and_actual_sql_only():
    corpus, hashes = load_corpus()
    cases = [c for c in corpus["queries"] if c["id"] in {"d-p-02", "d-p-04"}]
    report = {
        "corpus": hashes,
        "split": "development",
        "records": [
            {
                "id": c["id"],
                "retrieved_ids": c["relevant_chunk_ids"],
                "downstream": {
                    "sql": c["downstream"]["sql"],
                    "rows": [{"private": "NEVER_EXPORT"}],
                },
            }
            for c in cases
        ],
    }
    converted = samples(report)
    assert len(converted) == 2
    assert all(row["retrieved_contexts"] for row in converted)
    assert "NEVER_EXPORT" not in str(converted)
    for mutation in ("holdout", "hash", "missing-response"):
        changed = copy.deepcopy(report)
        if mutation == "holdout":
            changed["split"] = "holdout"
        elif mutation == "hash":
            changed["corpus"]["sha256"] = "wrong"
        else:
            changed["records"][0]["downstream"] = {}
        with pytest.raises(ValueError):
            samples(changed)
