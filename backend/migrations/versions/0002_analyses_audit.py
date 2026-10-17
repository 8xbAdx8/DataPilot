"""Analysis metadata and security audit events; no full result rows in PostgreSQL."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.String(36),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.String(1000), nullable=False),
        sa.Column("sql", sa.Text()),
        sa.Column("explanation", sa.String(1000)),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("returned_rows", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("dataset_id", "status", "created_at"):
        op.create_index(f"ix_analyses_{column}", "analyses", [column])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(36)),
        sa.Column("analysis_id", sa.String(36)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])


def downgrade():
    op.drop_table("audit_events")
    op.drop_table("analyses")
