"""Versioned local evidence snapshots. History reads never re-run retrieval."""

from copy import deepcopy

from sqlalchemy import or_, select

from app.models import KnowledgeChunk, KnowledgeDocument

VERSION = 1
EVIDENCE_STATUSES = {
    "resolved",
    "not_found",
    "ambiguous",
    "inapplicable",
    "insufficient_evidence",
    "not_configured",
    "not_evaluated",
}
DECISIONS = {"pending", "answer", "clarify", "reject", "error"}
CHECKS = {"not_requested", "approved", "rejected"}
EXECUTIONS = {"not_requested", "started", "success", "error"}
ORIGINS = {"vector_candidate", "registered_catalog", "agent_tool", "unrecorded"}


def begin_snapshot(rag, policy, status="not_evaluated", metric_ids=()):
    previous = rag.get("evidence", {})
    rag.setdefault("candidate_status", rag.get("status", "not_requested"))
    rag["evidence"] = {
        **previous,
        "version": VERSION,
        "policy": policy,
        "status": status if status in EVIDENCE_STATUSES else "not_evaluated",
        "source_origins": deepcopy(previous.get("source_origins", [])),
        "source_snapshots": [],
        "eligible_source_ids": list(metric_ids),
        "selected_source_ids": [],
        "context_source_ids": [],
        "used_source_ids": [],
        "sql_validation": "not_requested",
        "metric_validation": "not_requested",
        "execution": "not_requested",
        "final_decision": "pending",
    }


def capture_sources(session, owner_id, dataset_id, rag, sources, *, origin_hint="unrecorded"):
    """Resolve bounded IDs against authorization; never trust supplied titles/body.

    Stored analysis snapshots are authorized through their owning dataset. No
    rehydration against mutable knowledge happens on a history read.
    """
    evidence = rag["evidence"]
    supplied = {s["chunk_id"]: s for s in sources if isinstance(s.get("chunk_id"), str)}
    stages = rag.get("candidate_stages", [])[:5]
    initial = {
        c["chunk_id"]
        for stage in stages
        for c in stage.get("candidates", [])[:16]
        if isinstance(c.get("chunk_id"), str)
    }
    scores = {
        c["chunk_id"]: c.get("similarity")
        for stage in stages
        for c in stage.get("candidates", [])[:16]
        if isinstance(c.get("chunk_id"), str)
    }
    saved = {s["chunk_id"]: s for s in evidence["source_snapshots"]}
    ids = sorted(initial | set(supplied) | set(saved))[:96]
    if not ids:
        return []
    rows = session.execute(
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeChunk.id.in_(ids),
            KnowledgeDocument.owner_id == owner_id,
            or_(KnowledgeDocument.dataset_id == dataset_id, KnowledgeDocument.dataset_id.is_(None)),
            KnowledgeDocument.status == "ready",
        )
    ).all()
    current = {}
    for chunk, document in rows:
        incoming = supplied.get(chunk.id, {})
        score = incoming.get(
            "similarity", saved.get(chunk.id, {}).get("similarity", scores.get(chunk.id))
        )
        score = score if type(score) in (int, float) and 0 <= score <= 1 else None
        current[chunk.id] = {
            "chunk_id": chunk.id,
            "document_id": document.id,
            "chunk_index": chunk.chunk_index,
            "title": document.title,
            "source_type": document.source_type,
            "excerpt": chunk.content[:800],
            "similarity": score,
            "document_version": document.version,
        }
    # Keep already captured authorized snapshots intact if a document changes mid-run.
    saved.update({key: value for key, value in current.items() if key not in saved})
    evidence["source_snapshots"] = list(saved.values())[:96]
    allowed = set(saved)
    for stage in stages:
        stage["candidates"] = [
            c for c in stage.get("candidates", [])[:16] if c.get("chunk_id") in allowed
        ]
    origins = {
        s["chunk_id"]: s["origin"] for s in evidence["source_origins"] if s.get("origin") in ORIGINS
    }
    for key in saved:
        origins.setdefault(key, "vector_candidate" if key in initial else origin_hint)
    evidence["source_origins"] = [
        {"chunk_id": key, "origin": origin} for key, origin in origins.items() if key in allowed
    ]
    # Legacy API citations keep their established shape (no new version field).
    return [
        {k: v for k, v in current[key].items() if k != "document_version"}
        for key in supplied
        if key in current
    ]


def snapshot(run):
    value = run.rag.get("evidence", {})
    return value if value.get("version") == VERSION else None


def validate_live_sources(session, owner_id, dataset_id, rag, settings):
    """History stays immutable; a NEW execution must use current authorized rows.

    Source rows are locked in a stable order through the analysis commit. An
    update winning before the lock rejects this run; an update losing the lock
    waits until this execution ends. No silently refreshed/replaced citations.
    """
    from app.embeddings import get_embedding_provider
    from app.errors import AppError

    evidence = rag.get("evidence", {})
    ids = set(evidence.get("context_source_ids", []))
    if not ids:
        return
    expected = {s["chunk_id"]: s for s in evidence.get("source_snapshots", [])}
    rows = session.execute(
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeChunk.id.in_(ids),
            KnowledgeDocument.owner_id == owner_id,
            or_(KnowledgeDocument.dataset_id == dataset_id, KnowledgeDocument.dataset_id.is_(None)),
            KnowledgeDocument.status == "ready",
        )
        .order_by(KnowledgeDocument.id, KnowledgeChunk.id)
        .execution_options(populate_existing=True)
        .with_for_update(of=KnowledgeDocument)
    ).all()
    valid = len(ids) <= 4 and len(rows) == len(ids) and ids <= set(expected)
    try:
        provider = get_embedding_provider(settings)
    except AppError:
        valid, provider = False, None
    for chunk, document in rows:
        previous = expected.get(chunk.id, {})
        valid = valid and (
            previous.get("document_id") == document.id
            and previous.get("document_version") == document.version
            and previous.get("excerpt") == chunk.content[:800]
            and provider is not None
            and document.embedding_space == chunk.embedding_space == provider.space
            and chunk.embedding_model == provider.model
            and len(chunk.embedding) == 1536
        )
    evidence["live_source_validation"] = "approved" if valid else "rejected"
    if not valid:
        evidence.update(used_source_ids=[], final_decision="error")
        raise AppError(409, "evidence_source_stale", "知识来源已更新、删除或失效，请重新发起分析。")


def record_context(run, passed):
    evidence = snapshot(run)
    if evidence is not None and passed:
        allowed = {s["chunk_id"] for s in evidence["source_snapshots"]}
        ids = [s["chunk_id"] for s in run.rag.get("citations", []) if s["chunk_id"] in allowed]
        evidence["context_source_ids"] = list(dict.fromkeys(evidence["context_source_ids"] + ids))


def confirm_usage(run):
    evidence = snapshot(run)
    if evidence is None:
        return
    evidence["used_source_ids"] = []
    if (evidence["sql_validation"], evidence["metric_validation"], evidence["execution"]) != (
        "approved",
        "approved",
        "success",
    ):
        return
    proof = set(evidence["eligible_source_ids"]) & set(evidence["selected_source_ids"])
    proof &= {s["chunk_id"] for s in evidence["source_snapshots"]}
    evidence["used_source_ids"] = [s for s in evidence["context_source_ids"] if s in proof]


def audit_summary(run):
    evidence = snapshot(run)
    if evidence is None:
        return {}
    return {
        "evidence_version": VERSION,
        "evidence_status": evidence["status"],
        "final_decision": evidence["final_decision"],
        **{
            key.removesuffix("_ids") + "_count": len(evidence[key])
            for key in ("selected_source_ids", "context_source_ids", "used_source_ids")
        },
    }
