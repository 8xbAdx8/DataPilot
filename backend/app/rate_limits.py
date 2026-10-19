"""Atomic database-backed admission limits; callers commit with their operation."""

import math
from datetime import timedelta
from typing import Literal

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.auth import utc
from app.errors import AppError
from app.models import RateLimitBucket


def consume(session, user_id: str, scope: Literal["analysis", "upload"], settings, *, now=None):
    now = now or utc(session.scalar(select(func.current_timestamp())))
    limit = (
        settings.analysis_requests_per_window
        if scope == "analysis"
        else settings.upload_requests_per_window
    )
    dialect = session.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise AppError(503, "rate_limit_unavailable", "请求配额服务暂不可用。")
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    expires = now + timedelta(seconds=settings.rate_limit_window_seconds)
    old = RateLimitBucket
    expired = old.expires_at <= now
    statement = insert(old).values(user_id=user_id, scope=scope, count=1, expires_at=expires)
    statement = statement.on_conflict_do_update(
        index_elements=[old.user_id, old.scope],
        set_={
            "count": case((expired, 1), else_=old.count + 1),
            "expires_at": case((expired, expires), else_=old.expires_at),
        },
        where=or_(expired, old.count < limit),
    ).returning(old.count)
    if session.scalar(statement) is None:
        expiry = session.scalar(
            select(old.expires_at).where(old.user_id == user_id, old.scope == scope)
        )
        retry_after = max(1, math.ceil((utc(expiry) - now).total_seconds()))
        raise AppError(
            429,
            "rate_limited",
            f"请求过于频繁，请在 {retry_after} 秒后重试。",
            headers={"Retry-After": str(retry_after)},
            scope=scope,
            retry_after=retry_after,
        )
