import json

import pytest

from app.config import Settings
from app.rag18_evaluate import BASELINE, choose, configurations, reviewed, write
from app.tracing import retrieval_metadata, safe_metadata


def test_review_and_intent_separation():
    corpus, receipt = reviewed()
    assert receipt["status"] == "human-confirmed"
    assert len(corpus["documents"]) == 20
    assert len(corpus["queries"]) == 34
    assert len(configurations()) == 6
    assert BASELINE in configurations()


def test_selection_tie_keeps_current_default_and_excludes_errors():
    summary = {
        "cross_permission_leak_count": 0,
        "retrieval_errors": 0,
        "ranking": {"recall_at_k": 1, "mrr": 1},
        "no_answer_recall": 0,
    }
    profiles = [{"configuration": c, "summary": summary} for c in configurations()]
    assert choose(profiles) == BASELINE
    with pytest.raises(ValueError):
        choose([{**profiles[0], "summary": {**summary, "cross_permission_leak_count": 1}}])


def test_exclusive_evidence_cannot_be_overwritten(tmp_path):
    path = tmp_path / "evidence.json"
    write(path, {"original": True}, exclusive=True)
    with pytest.raises(FileExistsError):
        write(path, {}, exclusive=True)
    assert json.loads(path.read_bytes()) == {"original": True}


def test_candidate_metadata_omits_text_and_stable_identifiers():
    settings = Settings(_env_file=None)
    rag = {
        "candidate_stages": [
            {
                "stage": "selected",
                "candidates": [
                    {
                        "chunk_id": "SECRET-ID",
                        "document_id": "SECRET-DOC",
                        "excerpt": "SECRET-BODY",
                        "similarity": 0.75,
                    }
                ],
            }
        ]
    }
    first = retrieval_metadata(settings, rag, "salt1")
    second = retrieval_metadata(settings, rag, "salt2")
    assert first == safe_metadata(first)
    assert "SECRET" not in json.dumps(first)
    assert first["candidates"][0]["score_ppm"] == 750000
    assert first["candidates"] != second["candidates"]
    assert safe_metadata({"candidates": [{"stage": "selected", "excerpt": "SECRET"}]}) == {
        "candidates": []
    }
