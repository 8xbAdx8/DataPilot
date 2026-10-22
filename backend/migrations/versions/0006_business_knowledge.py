"""Scoped business knowledge and one explicitly versioned vector space."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector VERSION '0.8.7'")
    version = op.get_bind().scalar(
        sa.text("SELECT extversion FROM pg_extension WHERE extname='vector'")
    )
    if tuple(int(part) for part in version.split(".")) < (0, 8, 7):
        raise RuntimeError(
            "pgvector >=0.8.7 required; explicitly upgrade the shared extension first"
        )
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "dataset_id",
            sa.String(36),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scope_key", sa.String(36), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("embedding_space", sa.String(255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "owner_id", "scope_key", "content_hash", name="uq_knowledge_scope_hash"
        ),
        sa.CheckConstraint(
            "(dataset_id IS NULL AND scope_key = 'personal') OR "
            "(dataset_id IS NOT NULL AND scope_key = dataset_id)",
            name="ck_knowledge_scope",
        ),
    )
    for name, columns in (
        ("ix_knowledge_documents_owner_id", ["owner_id"]),
        ("ix_knowledge_documents_dataset_id", ["dataset_id"]),
        ("ix_knowledge_owner_scope_status", ["owner_id", "scope_key", "status"]),
    ):
        op.create_index(name, "knowledge_documents", columns)
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(1000), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("embedding_space", sa.String(255), nullable=False),
        sa.Column("embedding_model", sa.String(160), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunk_index"),
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"m": 16, "ef_construction": 64},
    )


def downgrade():
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    # Extension may be shared by other applications; never DROP EXTENSION CASCADE.
