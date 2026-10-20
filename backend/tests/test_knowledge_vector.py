import os
import secrets
import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import get_engine
from app.embeddings import FakeEmbeddingProvider
from app.models import KnowledgeChunk, KnowledgeDocument


@pytest.mark.postgres
def test_real_pgvector_storage_distance_and_index(session, auth_user):
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("Native vector operations require PostgreSQL")
    assert (
        session.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
        == "0.8.7"
    )
    document = KnowledgeDocument(
        owner_id=auth_user.id,
        scope_key="personal",
        title="metric",
        source_type="metric",
        content="revenue",
        content_hash=secrets.token_hex(32),
        status="ready",
    )
    session.add(document)
    session.flush()
    vector = FakeEmbeddingProvider().embed(["revenue"])[0]
    chunk = KnowledgeChunk(
        document_id=document.id,
        chunk_index=0,
        content="revenue",
        embedding=vector,
        embedding_model=FakeEmbeddingProvider.model,
        embedding_space=FakeEmbeddingProvider.space,
    )
    session.add(chunk)
    session.commit()
    distance = session.scalar(
        select(KnowledgeChunk.embedding.cosine_distance(vector)).where(
            KnowledgeChunk.id == chunk.id
        )
    )
    assert abs(distance) < 1e-6
    definition = session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_knowledge_chunks_embedding_hnsw'")
    )
    assert "USING hnsw" in definition and "vector_cosine_ops" in definition


@pytest.mark.postgres
def test_personal_scope_cannot_bypass_check_through_sql_null_semantics(session, auth_user):
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("Native database constraint regression requires PostgreSQL")
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            KnowledgeDocument(
                owner_id=auth_user.id,
                scope_key="wrong-scope",
                title="bad",
                source_type="rule",
                content="rule",
                content_hash=secrets.token_hex(32),
            )
        )
        session.flush()


@pytest.mark.postgres
def test_vector_migration_round_trip_preserves_platform_rows(monkeypatch):
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL for disposable migration database")
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = "datapilot_vector_test_" + secrets.token_hex(8)
    assert name.replace("_", "").isalnum()
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = True
        with monkeypatch.context() as patch:
            patch.setenv(
                "DATABASE_URL", admin.url.set(database=name).render_as_string(hide_password=False)
            )
            get_settings.cache_clear()
            get_engine.cache_clear()
            configuration = Config(
                "backend/alembic.ini" if os.path.exists("backend/alembic.ini") else "alembic.ini"
            )
            command.upgrade(configuration, "0005")
            engine = get_engine()
            row_id = str(uuid.uuid4())
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (id,username,password_hash,created_at) "
                        "VALUES (:id,:name,'unused-test-hash',:now)"
                    ),
                    {
                        "id": row_id,
                        "name": "migration-" + secrets.token_hex(8),
                        "now": datetime.now(UTC),
                    },
                )
            command.upgrade(configuration, "head")
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT COUNT(*) FROM knowledge_chunks")) == 0
                assert (
                    connection.scalar(
                        text("SELECT COUNT(*) FROM users WHERE id=:id"), {"id": row_id}
                    )
                    == 1
                )
            command.downgrade(configuration, "0005")
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT COUNT(*) FROM users WHERE id=:id"), {"id": row_id}
                    )
                    == 1
                )
                assert (
                    connection.scalar(
                        text("SELECT extversion FROM pg_extension WHERE extname='vector'")
                    )
                    == "0.8.7"
                )
            command.upgrade(configuration, "head")
            command.check(configuration)
            engine.dispose()
    finally:
        get_engine.cache_clear()
        get_settings.cache_clear()
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()
