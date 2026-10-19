"""Shared request budgets and bounded per-analysis telemetry, without result data."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rate_limit_buckets",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("scope", sa.String(16), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rate_limit_buckets_expires_at", "rate_limit_buckets", ["expires_at"])
    op.add_column("analyses", sa.Column("request_id", sa.String(36), nullable=True))
    op.create_index("ix_analyses_request_id", "analyses", ["request_id"])
    op.add_column(
        "analyses",
        sa.Column("telemetry", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("analyses", "telemetry", server_default=None)


def downgrade():
    op.drop_column("analyses", "telemetry")
    op.drop_index("ix_analyses_request_id", table_name="analyses")
    op.drop_column("analyses", "request_id")
    op.drop_table("rate_limit_buckets")
