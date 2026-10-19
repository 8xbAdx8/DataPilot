import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_started: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Existing uploads remain unowned and inaccessible until an operator assigns them.
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(10))
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer)
    column_count: Mapped[int] = mapped_column(Integer)
    profile: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(String(1000))
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), index=True, default="running")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    returned_rows: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    telemetry: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
