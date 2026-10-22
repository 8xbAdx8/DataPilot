import json
from pathlib import Path

import pytest

from app.embeddings import FakeEmbeddingProvider
from app.main import app
from app.rag24_budget import BudgetStop
from app.rag24_evaluate import corpus, pool, run_database, summarize, verify_freeze, write_new


def test_new_review_binds_exact_development_labels_and_keeps_original_26():
    value = corpus("development-v1")
    previous = json.loads(
        (Path(__file__).parents[1] / "app/evals/rag22/corpus-v1.json").read_bytes()
    )
    assert len(value["queries"]) == 36 and len(value["documents"]) == 24
    assert value["documents"][:18] == previous["documents"]
    assert [
        {k: q[k] for k in old}
        for q, old in zip(value["queries"][:26], previous["queries"], strict=True)
    ] == previous["queries"]
    assert len(pool(value)) == 60


def test_artifacts_are_exclusive_and_scores_do_not_invent_adoption(tmp_path):
    path = tmp_path / "artifact.json"
    write_new(path, {"state": "complete"})
    with pytest.raises(FileExistsError):
        write_new(path, {"state": "different"})
    result = summarize(
        {
            "documents": [],
            "queries": [
                {
                    "id": "p",
                    "kind": "knowledge",
                    "relevant_ids": ["good"],
                    "expected_evidence": "knowledge",
                }
            ],
        },
        [{"id": "p", "authorized_ids": ["good"], "a": {"ids": ["good"], "authenticity": [True]}}],
        "a",
    )
    assert result["knowledge"]["precision_at_k"] == 0.25
    assert result["knowledge"]["recall_at_k"] == 1
    assert result["agent_selection"] == result["sql_execution"] == "NOT_RUN"


def test_original_index_and_retrieval_interfaces_work_with_cached_vectors(
    session, settings, monkeypatch
):
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("Native pgvector integration requires PostgreSQL")
    value = corpus("development-v1")
    fake = FakeEmbeddingProvider()
    vectors = dict(zip(pool(value), fake.embed(pool(value)), strict=True))
    monkeypatch.setattr(fake, "embed", lambda texts: [vectors[t] for t in texts])
    monkeypatch.setattr("app.knowledge.get_embedding_provider", lambda _: fake)
    monkeypatch.setattr("app.embeddings.get_embedding_provider", lambda _: fake)
    settings.knowledge_requests_per_window = 120
    monkeypatch.setattr(app, "dependency_overrides", {})
    result = run_database(
        session,
        settings,
        value,
        [
            {"id": "a", "policy": "legacy", "threshold": 0.7, "strategy": "vector"},
            {"id": "b", "policy": "registered", "threshold": 0.7, "strategy": "vector"},
        ],
        fake,
    )
    assert len(result) == 36
    for strategy in ("a", "b"):
        scores = summarize(value, result, strategy)
        assert scores["reference_errors"] == scores["unauthorized_leaks"] == 0
    assert summarize(value, result, "b")["knowledge"]["recall_at_k"] == 0


def test_holdout_is_independent_reviewed_and_has_exact_frozen_group_sizes():
    from collections import Counter

    development, holdout = corpus("development-v1"), corpus("holdout-v1")
    assert Counter(q["kind"] for q in holdout["queries"]) == {
        "metric": 10,
        "knowledge": 10,
        "negative": 10,
        "safety": 4,
    }
    assert not {q["intent_group"] for q in development["queries"]} & {
        q["intent_group"] for q in holdout["queries"]
    }
    assert not {q["query"] for q in development["queries"]} & {
        q["query"] for q in holdout["queries"]
    }


def test_freeze_is_portable_across_line_endings_but_rejects_content_change(tmp_path, monkeypatch):
    import hashlib

    monkeypatch.setattr("app.rag24_evaluate.ROOT", tmp_path)
    file = tmp_path / "source.py"
    file.write_bytes(b"safe = True\r\n")
    freeze = {
        "hash_mode": "canonical-lf",
        "source_sha256": {"source.py": hashlib.sha256(b"safe = True\n").hexdigest()},
        "asset_sha256": {},
    }
    verify_freeze(freeze)
    file.write_bytes(b"safe = False\r\n")
    with pytest.raises(BudgetStop, match="frozen_file_changed"):
        verify_freeze(freeze)


def test_holdout_admission_uses_canonical_check_before_any_database(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    import app.rag24_evaluate as evaluator

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (tmp_path / "docs/rag24").mkdir(parents=True)
    (fixtures / "holdout-v1.json").write_bytes(b"{}\n")
    (tmp_path / "source.py").write_bytes(b"safe = True\r\n")
    freeze = {
        "hash_mode": "canonical-lf",
        "source_sha256": {"source.py": hashlib.sha256(b"safe = True\n").hexdigest()},
        "asset_sha256": {},
        "corpus_sha256": hashlib.sha256(b"{}\n").hexdigest(),
        "configurations": [],
    }
    frozen = fixtures / "freeze.json"
    frozen.write_text(json.dumps(freeze), encoding="utf-8")
    for key, value in {
        "ROOT": tmp_path,
        "FIXTURES": fixtures,
        "LIVE_ROOT": tmp_path / "live",
        "HOLDOUT_FREEZE": frozen,
    }.items():
        monkeypatch.setattr(evaluator, key, value)
    monkeypatch.setattr(evaluator, "corpus", lambda _: {"documents": [], "queries": []})
    monkeypatch.setattr(evaluator, "Settings", lambda: None)
    monkeypatch.setattr(
        evaluator,
        "live_ledger",
        lambda _: SimpleNamespace(cache=lambda _: [], export=lambda: {"http_requests_reserved": 0}),
    )
    monkeypatch.setattr(
        evaluator, "CachedRealProvider", lambda *_: SimpleNamespace(space="synthetic")
    )

    attempts = []

    def reached_database():
        attempts.append(True)
        raise BudgetStop("database_reached_after_valid_admission")

    monkeypatch.setattr(evaluator, "test_engine", reached_database)
    with pytest.raises(BudgetStop, match="database_reached_after_valid_admission"):
        evaluator.evaluate("holdout-v1", "synthetic-admission", [])
    assert (tmp_path / "live/holdout-v1.claim.json").exists()
    with pytest.raises(FileExistsError):
        evaluator.evaluate("holdout-v1", "different-output-cannot-replay", [])
    assert len(attempts) == 1
