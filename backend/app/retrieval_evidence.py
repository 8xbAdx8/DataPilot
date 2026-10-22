"""Deterministic evidence selection; cosine similarity never establishes a metric."""

from app.metric_resolution import MetricRegistry


def assess_evidence(registry, question, candidates):
    """Only validated, uniquely named registered definitions are answer evidence.

    This is deliberately NOT a general semantic relevance classifier. Prose is
    discoverable in legacy search but cannot become evidence in registered mode.
    Catalog supplementation has no vector score and is explicitly attributed.
    """
    resolution = registry.resolve(question, question=True)
    status = resolution["status"] if registry.enabled else "not_configured"
    by_chunk = {item["chunk_id"]: item for item in candidates}
    sources, origins = [], []
    if status == "resolved":
        for item in resolution["items"]:
            source = item["source"].copy()
            matched = by_chunk.get(source["chunk_id"])
            # The persisted registry body, never candidate-supplied content, wins.
            same = (
                matched is not None
                and matched.get("similarity") is not None
                and all(matched.get(k) == source[k] for k in ("document_id", "excerpt"))
            )
            origin = "vector_candidate" if same else "registered_catalog"
            if same:
                source["similarity"] = matched.get("similarity")
            origins.append({"chunk_id": source["chunk_id"], "origin": origin})
            sources.append(source)
    summary = {
        "policy": "registered",
        "status": status,
        "candidate_count": len(candidates),
        "matched_definition_count": len(resolution["items"]),
        "eligible_candidate_count": sum(o["origin"] == "vector_candidate" for o in origins),
        "catalog_supplement_count": sum(o["origin"] == "registered_catalog" for o in origins),
        "source_origins": origins,
        "selected_source_ids": [],
        "used_source_ids": [],
        "final_decision": "pending",
    }
    return sources, summary


def filter_evidence(session, owner_id, dataset_id, question, settings, result):
    registry = MetricRegistry(session, owner_id, dataset_id, settings)
    sources, summary = assess_evidence(registry, question, result.get("citations", []))
    return {
        **result,
        "candidate_status": result.get("candidate_status", result["status"]),
        "status": "retrieved"
        if sources
        else (result["status"] if result["status"] in {"error", "not_configured"} else "no_match"),
        "citations": sources,
        "evidence": summary,
    }
