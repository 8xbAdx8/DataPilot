import copy
import math

import pytest

from app.rag_evaluate import load_corpus
from app.rag_metrics import rank_scores, summarize_rag


def test_rank_scores_by_hand():
    scored = rank_scores(["x", "a", "b"], ["a", "b"], 4)
    assert scored["precision_at_k"] == 0.5
    assert scored["recall_at_k"] == 1
    assert scored["mrr"] == 0.5
    assert scored["ndcg_at_k"] == pytest.approx((1 / math.log2(3) + 0.5) / (1 + 1 / math.log2(3)))
    assert all(v is None for v in rank_scores([], [], 4).values())
    with pytest.raises(ValueError):
        rank_scores(["a", "a"], ["a"], 4)
    with pytest.raises(ValueError):
        rank_scores([], [], 0)


def test_corpus_review_and_scope():
    corpus, hashes = load_corpus()
    assert hashes["review_status"] == "human-confirmed"
    assert len(corpus["queries"]) == 44
    assert len(corpus["documents"]) == 23
    assert sum(c["run_downstream"] for c in corpus["queries"]) == 9
    dev = {c["family"] for c in corpus["queries"] if c["split"] == "development"}
    hold = {c["family"] for c in corpus["queries"] if c["split"] == "holdout"}
    assert not dev & hold


def test_errors_are_not_successful_abstention_and_downstream_is_not_implied():
    corpus, _ = load_corpus()
    case = next(c for c in corpus["queries"] if c["id"] == "d-u-tax")
    record = dict(
        id=case["id"],
        retrieved_ids=[],
        authorized_chunk_ids=[],
        retrieval_status="error",
        retrieval_ms=3,
    )
    score = summarize_rag([case], [record])
    assert score["no_answer_recall"] == 0
    assert score["no_answer_precision"] is None
    assert score["sql_result_accuracy"] is None
    assert score["wrong_knowledge_adoption_rate"] is None
    record["retrieval_status"] = "no_match"
    assert summarize_rag([case], [record])["no_answer_recall"] == 1
    record["retrieved_ids"] = ["foreign"]
    assert summarize_rag([case], [record])["cross_permission_leak_count"] == 1
    with pytest.raises(ValueError):
        summarize_rag([case], [record, copy.deepcopy(record)])


def test_wrong_adoption_separate_from_retrieval_and_actual_rows():
    corpus, _ = load_corpus()
    case = next(c for c in corpus["queries"] if c["id"] == "d-p-02")
    record = dict(
        id=case["id"],
        retrieved_ids=case["relevant_chunk_ids"],
        authorized_chunk_ids=case["relevant_chunk_ids"],
        retrieval_status="retrieved",
        retrieval_ms=3,
        downstream=dict(adopted_ids=["wrong"], cited_ids=[], rows=[{"wrong": 30}]),
    )
    score = summarize_rag([case], [record])
    assert score["retrieval_hit_rate"] == 1
    assert score["correct_knowledge_adoption_rate"] == 0
    assert score["wrong_knowledge_adoption_rate"] == 1
    assert score["sql_result_accuracy"] == 0
