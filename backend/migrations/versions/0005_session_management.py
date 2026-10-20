"""Public session identifiers and honest creation timestamps.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    # Core PostgreSQL function; generates a different public ID for every existing row.
    op.add_column(
        "auth_sessions",
        sa.Column(
            "id", sa.String(36), nullable=False, server_default=sa.text("gen_random_uuid()::text")
        ),
    )
    op.create_unique_constraint("uq_auth_sessions_id", "auth_sessions", ["id"])
    op.alter_column("auth_sessions", "id", server_default=None)
    op.add_column(
        "auth_sessions", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("auth_sessions", "created_at")
    op.drop_constraint("uq_auth_sessions_id", "auth_sessions", type_="unique")
    op.drop_column("auth_sessions", "id")
