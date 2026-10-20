"""Deterministic retrieval metrics from explicitly reviewed source IDs."""

import math
import statistics

from app.evaluate import same_rows


def mean(values):
    return statistics.mean(values) if values else None


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def rank_scores(retrieved, relevant, k):
    if k < 1 or len(set(retrieved)) != len(retrieved):
        raise ValueError("K must be positive; duplicate sources are not independent hits")
    relevant = set(relevant)
    if not relevant:
        return {name: None for name in ("precision_at_k", "recall_at_k", "mrr", "ndcg_at_k")}
    hits = [int(source in relevant) for source in retrieved[:k]]
    first = next((i for i, value in enumerate(hits, 1) if value), None)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
    return {
        "precision_at_k": sum(hits) / k,
        "recall_at_k": sum(hits) / len(relevant),
        "mrr": 1 / first if first else 0.0,
        "ndcg_at_k": sum(value / math.log2(i + 1) for i, value in enumerate(hits, 1)) / ideal,
    }


def summarize_rag(cases, records, k=4):
    known = {c["id"]: c for c in cases}
    if len({r["id"] for r in records}) != len(records) or any(
        r["id"] not in known for r in records
    ):
        raise ValueError("Unknown or repeated evaluation record")
    ranks, adopted, cited, sql = [], [], [], []
    no_answer, empty_predictions, correct_empty, false_retrievals = 0, 0, 0, 0
    leaks, retrieval_errors, denied = 0, 0, 0
    hit, hit_count, correct_adoption, correct_adoption_count = 0, 0, 0, 0
    for record in records:
        case = known[record["id"]]
        sources = record.get("retrieved_ids", [])
        leaks += len(set(sources) - set(record["authorized_chunk_ids"]))
        if not case["authorized"]:
            denied += record["retrieval_status"] == "denied" and not sources
            continue
        relevant = set(case["relevant_chunk_ids"])
        ranks.append(rank_scores(sources, relevant, k))
        retrieval_errors += record["retrieval_status"] == "error"
        empty = not sources and record["retrieval_status"] in {
            "no_match",
            "no_documents",
            "space_mismatch",
        }
        expected_empty = case["should_no_match"]
        no_answer += expected_empty
        empty_predictions += empty
        correct_empty += empty and expected_empty
        false_retrievals += expected_empty and bool(sources)
        if relevant:
            hit += bool(set(sources) & relevant)
            hit_count += 1
        downstream = record.get("downstream")
        if downstream is not None:
            selected = set(downstream.get("adopted_ids", []))
            accepted = set(case["acceptable_source_ids"])
            adopted.append(bool(selected - accepted))
            if relevant:
                correct_adoption += bool(selected & relevant)
                correct_adoption_count += 1
                cited.append(bool(set(downstream.get("cited_ids", [])) & relevant))
            if "rows" in case["downstream"]:
                sql.append(same_rows(downstream.get("rows"), case["downstream"]["rows"]))
    return {
        "cases": len(records),
        "positive_cases": hit_count,
        "ranking": {
            key: mean([r[key] for r in ranks if r[key] is not None])
            for key in rank_scores([], [], k)
        },
        "retrieval_hit_rate": ratio(hit, hit_count),
        "no_answer_cases": no_answer,
        "no_answer_precision": ratio(correct_empty, empty_predictions),
        "no_answer_recall": ratio(correct_empty, no_answer),
        "false_retrieval_rate": ratio(false_retrievals, no_answer),
        "retrieval_errors": retrieval_errors,
        "cross_permission_leak_count": leaks,
        "authorization_denials": denied,
        "downstream_cases": len(adopted),
        "wrong_knowledge_adoption_rate": mean(adopted),
        "correct_knowledge_adoption_rate": ratio(correct_adoption, correct_adoption_count),
        "citation_hit_rate": mean(cited),
        "sql_result_accuracy": mean(sql),
        "sql_labeled_cases_run": len(sql),
        "retrieval_latency_p50_ms": statistics.median(r["retrieval_ms"] for r in records)
        if records
        else None,
    }
