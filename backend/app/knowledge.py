"""Owner-scoped business definitions, atomic indexes and permission-first vector search."""

import hashlib
import re
import time
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth import UserDep, utc
from app.datasets import SessionDep, SettingsDep, find_dataset
from app.embeddings import get_embedding_provider, validate_vectors
from app.errors import AppError
from app.models import AuditEvent, KnowledgeChunk, KnowledgeDocument, User
from app.rate_limits import consume
from app.retrieval import select_candidates

router = APIRouter(prefix="/api", tags=["knowledge"])
SourceType = Literal[
    "data_dictionary",
    "field_alias",
    "metric_definition",
    "dataset_description",
    "business_rule",
    "analysis_example",
]
SECRET = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{12,}|\bgh[pousr]_[A-Za-z0-9]{16,}|BEGIN [A-Z ]*PRIVATE KEY|"
    r"\bBearer\s+[A-Za-z0-9_.-]{12,}|(?:api[_ -]?key|password|datapilot_session)"
    r"\s*[\"']?\s*[:=]\s*[\"']?[^\s\"',}]{8,})",
    re.I,
)


class KnowledgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    source_type: SourceType
    content: str = Field(min_length=1, max_length=16000)

    @field_validator("title", "content")
    @classmethod
    def safe_text(cls, value):
        value = value.replace("\r\n", "\n").strip()
        if (
            not value
            or any(ord(c) < 32 and c not in "\n\t" for c in value)
            or len(value.encode()) > 32768
        ):
            raise ValueError("知识内容为空、含控制字符或超过大小限制。")
        if SECRET.search(value):
            raise ValueError("知识条目疑似包含凭据，请去除密钥、密码和会话令牌。")
        return value


class DocumentView(BaseModel):
    id: str
    dataset_id: str | None
    title: str
    source_type: str
    status: str
    error_code: str | None
    embedding_space: str | None
    fake_embedding: bool
    chunk_count: int
    version: int
    content: str | None = None
    duplicate: bool = False
    created_at: datetime
    updated_at: datetime


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=1000)


def content_hash(body):
    return hashlib.sha256((body.source_type + "\n" + body.content).encode()).hexdigest()


def split_content(content: str):
    # Deterministic bounded chunks; no row/file ingestion, code execution or HTML rendering.
    return [content[start : start + 800] for start in range(0, len(content), 700)][:24]


def owned_document(session, document_id, owner_id, *, lock=False):
    query = select(KnowledgeDocument).where(
        KnowledgeDocument.id == str(document_id), KnowledgeDocument.owner_id == owner_id
    )
    if lock:
        query = query.with_for_update()
    saved = session.scalar(query.execution_options(populate_existing=True))
    if saved is None:
        raise AppError(404, "knowledge_not_found", "知识条目不存在。")
    if saved.dataset_id:
        find_dataset(uuid.UUID(saved.dataset_id), session, owner_id)
    return saved


def document_view(session, saved, *, full=False, duplicate=False):
    count = session.scalar(
        select(func.count())
        .select_from(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == saved.id)
    )
    return DocumentView(
        id=saved.id,
        dataset_id=saved.dataset_id,
        title=saved.title,
        source_type=saved.source_type,
        status=saved.status,
        error_code=saved.error_code,
        embedding_space=saved.embedding_space,
        fake_embedding=bool(saved.embedding_space and saved.embedding_space.startswith("fake/")),
        chunk_count=count,
        version=saved.version,
        content=saved.content if full else None,
        duplicate=duplicate,
        created_at=utc(saved.created_at),
        updated_at=utc(saved.updated_at),
    )


def index_document(session, document_id, owner_id, settings, version):
    saved = owned_document(session, document_id, owner_id)
    if saved.version != version:
        raise AppError(409, "knowledge_index_superseded", "知识已更新，请刷新索引状态。")
    chunks = split_content(saved.content)
    try:
        provider = get_embedding_provider(settings)
        vectors = validate_vectors(provider.embed(chunks), len(chunks))
    except AppError as error:
        session.rollback()
        saved = owned_document(session, document_id, owner_id, lock=True)
        if saved.version == version:
            saved.status, saved.error_code = "failed", error.detail["code"]
            session.add(
                AuditEvent(
                    event="knowledge_index_failed",
                    dataset_id=saved.dataset_id,
                    details={"document_id": saved.id, "reason": saved.error_code},
                )
            )
            session.commit()
        return document_view(session, saved, full=True)
    saved = owned_document(session, document_id, owner_id, lock=True)
    if saved.version != version:
        session.rollback()
        raise AppError(409, "knowledge_index_superseded", "知识已更新，旧索引结果已丢弃。")
    session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == saved.id))
    for index, (content, vector) in enumerate(zip(chunks, vectors, strict=True)):
        session.add(
            KnowledgeChunk(
                document_id=saved.id,
                chunk_index=index,
                content=content,
                embedding=vector,
                embedding_space=provider.space,
                embedding_model=provider.model,
                details={"source_type": saved.source_type},
            )
        )
    saved.status, saved.error_code, saved.embedding_space = "ready", None, provider.space
    session.add(
        AuditEvent(
            event="knowledge_indexed",
            dataset_id=saved.dataset_id,
            details={"document_id": saved.id, "chunks": len(chunks), "source": provider.source},
        )
    )
    session.commit()
    return document_view(session, saved, full=True)


@router.get("/datasets/{dataset_id}/knowledge", response_model=list[DocumentView])
def list_documents(dataset_id: uuid.UUID, session: SessionDep, user: UserDep):
    find_dataset(dataset_id, session, user.id)
    docs = session.scalars(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.owner_id == user.id, KnowledgeDocument.dataset_id == str(dataset_id)
        )
        .order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id)
        .limit(100)
    ).all()
    return [document_view(session, row) for row in docs]


@router.post("/datasets/{dataset_id}/knowledge", response_model=DocumentView, status_code=201)
def add_document(
    dataset_id: uuid.UUID,
    body: KnowledgeInput,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    user: UserDep,
):
    find_dataset(dataset_id, session, user.id)
    consume(session, user.id, "knowledge", settings)
    session.scalar(select(User.id).where(User.id == user.id).with_for_update())
    fingerprint = content_hash(body)
    existing = session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.owner_id == user.id,
            KnowledgeDocument.scope_key == str(dataset_id),
            KnowledgeDocument.content_hash == fingerprint,
        )
    )
    if existing:
        session.commit()
        response.status_code = 200
        return document_view(session, existing, full=True, duplicate=True)
    count = session.scalar(
        select(func.count())
        .select_from(KnowledgeDocument)
        .where(KnowledgeDocument.owner_id == user.id)
    )
    if count >= 100:
        raise AppError(409, "knowledge_limit", "每个账户最多保留 100 个知识条目。")
    saved = KnowledgeDocument(
        owner_id=user.id,
        dataset_id=str(dataset_id),
        scope_key=str(dataset_id),
        title=body.title,
        source_type=body.source_type,
        content=body.content,
        content_hash=fingerprint,
        status="pending",
    )
    session.add(saved)
    session.flush()
    document_id, version = saved.id, saved.version
    session.add(
        AuditEvent(
            event="knowledge_added",
            dataset_id=str(dataset_id),
            details={"document_id": document_id},
        )
    )
    session.commit()  # No business row lock is held across the remote embedding request.
    return index_document(session, document_id, user.id, settings, version)


@router.get("/knowledge/{document_id}", response_model=DocumentView)
def get_document(document_id: uuid.UUID, session: SessionDep, user: UserDep):
    return document_view(session, owned_document(session, document_id, user.id), full=True)


@router.put("/knowledge/{document_id}", response_model=DocumentView)
def update_document(
    document_id: uuid.UUID,
    body: KnowledgeInput,
    session: SessionDep,
    settings: SettingsDep,
    user: UserDep,
):
    consume(session, user.id, "knowledge", settings)
    saved = owned_document(session, document_id, user.id, lock=True)
    saved.title, saved.source_type, saved.content = body.title, body.source_type, body.content
    saved.content_hash, saved.version = content_hash(body), saved.version + 1
    saved.status, saved.error_code = "pending", None
    version = saved.version
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise AppError(
            409, "knowledge_duplicate", "同一作用域已有相同内容，未覆盖现有条目。"
        ) from error
    return index_document(session, str(document_id), user.id, settings, version)


@router.post("/knowledge/{document_id}/reindex", response_model=DocumentView)
def reindex_document(
    document_id: uuid.UUID, session: SessionDep, settings: SettingsDep, user: UserDep
):
    consume(session, user.id, "knowledge", settings)
    saved = owned_document(session, document_id, user.id, lock=True)
    saved.status, saved.error_code, saved.version = "pending", None, saved.version + 1
    version = saved.version
    session.commit()
    return index_document(session, str(document_id), user.id, settings, version)


@router.delete("/knowledge/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, session: SessionDep, user: UserDep):
    saved = owned_document(session, document_id, user.id, lock=True)
    session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == saved.id))
    session.add(
        AuditEvent(
            event="knowledge_deleted",
            dataset_id=saved.dataset_id,
            details={"document_id": saved.id},
        )
    )
    session.delete(saved)
    session.commit()
    return Response(status_code=204)


def eligible_chunks(owner_id, dataset_id, space):
    # Materialize authorized rows BEFORE top-k/distance. An ANN scan over all users
    # followed by application filtering is deliberately not used.
    return (
        select(
            KnowledgeChunk.id.label("chunk_id"),
            KnowledgeChunk.document_id,
            KnowledgeChunk.chunk_index,
            KnowledgeChunk.content,
            KnowledgeChunk.embedding,
            KnowledgeDocument.title,
            KnowledgeDocument.source_type,
        )
        .join(KnowledgeDocument)
        .where(
            KnowledgeDocument.owner_id == owner_id,
            or_(KnowledgeDocument.dataset_id == dataset_id, KnowledgeDocument.dataset_id.is_(None)),
            KnowledgeDocument.status == "ready",
            KnowledgeDocument.embedding_space == space,
            KnowledgeChunk.embedding_space == space,
        )
        .cte("authorized_knowledge")
        .prefix_with("MATERIALIZED")
    )


def citation(row, score=None):
    return {
        "chunk_id": row.chunk_id,
        "document_id": row.document_id,
        "chunk_index": row.chunk_index,
        "title": row.title,
        "source_type": row.source_type,
        "excerpt": row.content[:800],
        "similarity": round(score, 6) if score is not None else None,
    }


def retrieve_knowledge(session, owner_id, dataset_id, question, settings, *, deadline=None):
    find_dataset(uuid.UUID(dataset_id), session, owner_id)
    result = {
        "status": "not_configured",
        "source": "unavailable",
        "used": False,
        "citations": [],
        "error_code": None,
    }
    try:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppError(408, "workflow_timeout", "工作流超过总时间限制。")
            settings = settings.model_copy(
                update={
                    "embedding_timeout_seconds": min(settings.embedding_timeout_seconds, remaining)
                }
            )
        provider = get_embedding_provider(settings)
        result["source"] = provider.source
        if session.get_bind().dialect.name != "postgresql":
            raise AppError(
                503, "vector_backend_required", "知识相似度检索需要 PostgreSQL pgvector。"
            )
        available = session.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(
                KnowledgeDocument.owner_id == owner_id,
                or_(
                    KnowledgeDocument.dataset_id == dataset_id,
                    KnowledgeDocument.dataset_id.is_(None),
                ),
                KnowledgeDocument.status == "ready",
            )
        )
        if not available:
            result["status"] = "no_documents"
            return result
        allowed = eligible_chunks(owner_id, dataset_id, provider.space)
        if not session.scalar(select(func.count()).select_from(allowed)):
            result["status"] = "space_mismatch"
            return result
        vector = validate_vectors(provider.embed([question]), 1)[0]
        if deadline is not None:
            settings = settings.model_copy(
                update={
                    "reranker_timeout_seconds": min(
                        settings.reranker_timeout_seconds, max(0.001, deadline - time.monotonic())
                    )
                }
            )
        result["candidate_stages"] = []
        result["citations"] = select_candidates(
            session, allowed, vector, question, settings, citation, result["candidate_stages"]
        )
        result["status"] = "retrieved" if result["citations"] else "no_match"
        return result
    except AppError as error:
        result["status"] = (
            "not_configured" if error.detail["code"] == "embedding_not_configured" else "error"
        )
        result["error_code"] = error.detail["code"]
        return result


@router.post("/datasets/{dataset_id}/knowledge/search")
def search_documents(
    dataset_id: uuid.UUID,
    body: SearchInput,
    session: SessionDep,
    settings: SettingsDep,
    user: UserDep,
):
    find_dataset(dataset_id, session, user.id)
    consume(session, user.id, "knowledge", settings)
    session.commit()
    return retrieve_knowledge(session, user.id, str(dataset_id), body.query, settings)


def metric_definition(session, owner_id, dataset_id, name, settings):
    from app.metric_resolution import MetricRegistry

    return MetricRegistry(session, owner_id, dataset_id, settings).lookup(name)
