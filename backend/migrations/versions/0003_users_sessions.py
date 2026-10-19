"""Account sessions and ownership; never automatically claim existing datasets."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "login_attempts",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_started", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("datasets", sa.Column("owner_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_datasets_owner_id", "datasets", "users", ["owner_id"], ["id"])
    op.create_index("ix_datasets_owner_id", "datasets", ["owner_id"])


def downgrade():
    op.drop_index("ix_datasets_owner_id", table_name="datasets")
    op.drop_constraint("fk_datasets_owner_id", "datasets", type_="foreignkey")
    op.drop_column("datasets", "owner_id")
    op.drop_table("login_attempts")
    op.drop_table("auth_sessions")
    op.drop_table("users")
