"""Frozen deterministic retrieval acceptance. Never infers Agent/SQL success."""


def judge(strategies, ledger, contract, *, references_persisted, cleanup_ok):
    chosen = strategies[contract["selected_strategy"]]
    checks = {}
    for kind, count in contract["expected_counts"].items():
        checks[kind + "_case_count"] = chosen[kind]["cases"] == count
    checks["metric_recall"] = chosen["metric"]["recall_at_k"] >= contract["min_metric_recall"]
    checks["knowledge_recall"] = (
        chosen["knowledge"]["recall_at_k"] >= contract["min_knowledge_recall"]
    )
    negative = chosen["negative"]
    checks["negative_refusal"] = (
        negative["cases"] > 0
        and negative["correct_no_match"] / negative["cases"] >= contract["min_negative_refusal"]
    )
    for key, limit in (
        ("unauthorized_leaks", "max_unauthorized_leaks"),
        ("reference_errors", "max_reference_errors"),
        ("malicious_sources_returned", "max_malicious_evidence"),
    ):
        checks[key] = chosen[key] <= contract[limit]
    # Empty or partially written ledgers do not pass by vacuous truth.
    rows = ledger["requests"]
    checks["budget"] = (
        0
        < len(rows)
        == ledger["http_requests_reserved"]
        <= contract["budget"]["max_embedding_requests"]
        and ledger["known_input_tokens"] <= contract["budget"]["max_input_tokens"]
        and ledger["usage_complete"]
        and ledger["stopped"] is None
        and all(
            row["status"] == "complete"
            and row["provider_request_id"]
            and type(row["actual"]) is int
            and row["actual"] > 0
            and row["actual"] <= row["reserved"]
            and row["usage"]["prompt_tokens"] == row["usage"]["total_tokens"] == row["actual"]
            for row in rows
        )
        and sum(row["actual"] for row in rows) == ledger["known_input_tokens"]
    )
    checks["cross_connection_persistence"] = references_persisted is True
    checks["cleanup"] = cleanup_ok is True
    return {
        "status": "PASS" if all(checks.values()) else "QUALITY_NOT_PASSED",
        "scope": contract["scope"],
        "checks": checks,
        "chat_requests": 0,
        "langsmith_requests": 0,
        "agent_adoption": "NOT_RUN",
        "sql_execution": "NOT_RUN",
        "unsafe_sql_execution": {
            "calls": 0,
            "reason": "retrieval-only runner has no analysis execute path",
        },
    }
