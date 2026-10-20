"""Opaque, revocable server sessions. No tokens or passwords in JSON responses."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from threading import BoundedSemaphore
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.errors import AppError
from app.models import AuditEvent, AuthSession, LoginAttempt, User

router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE = "datapilot_session"
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
PASSWORD_SLOTS = BoundedSemaphore(2)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalize_username(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", value):
        raise ValueError("用户名须为 3–64 位英文、数字、点、下划线或短横线。")
    return value


def derive(password: str, salt: bytes) -> bytes:
    if not PASSWORD_SLOTS.acquire(blocking=False):
        raise AppError(503, "auth_busy", "登录服务繁忙，请稍后重试。")
    try:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**17,
            r=8,
            p=1,
            dklen=64,
            maxmem=256 * 1024 * 1024,
        )
    finally:
        PASSWORD_SLOTS.release()


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("密码长度须为 12–128 个字符。")
    salt = secrets.token_bytes(16)
    return f"scrypt-v1${salt.hex()}${derive(password, salt).hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        version, salt, expected = encoded.split("$")
        if version != "scrypt-v1" or len(salt) != 32 or len(expected) != 128:
            return False
        return hmac.compare_digest(derive(password, bytes.fromhex(salt)), bytes.fromhex(expected))
    except ValueError:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def lock_attempt(session: Session, username: str) -> LoginAttempt:
    """First lock in every credential operation; no intermediate commits.

    A savepoint handles concurrent first inserts. Retention can remove an expired
    row, so re-read under lock and repeat rather than trusting a stale identity map.
    """
    key = token_hash(username)
    while True:
        attempt = session.scalar(
            select(LoginAttempt)
            .where(LoginAttempt.key == key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is not None:
            if utc(attempt.window_started) <= datetime.now(UTC) - timedelta(minutes=15):
                attempt.failures, attempt.window_started = 0, datetime.now(UTC)
            return attempt
        try:
            with session.begin_nested():
                session.add(LoginAttempt(key=key, failures=0, window_started=datetime.now(UTC)))
                session.flush()
        except IntegrityError:
            pass  # Another worker inserted first; only the savepoint was rolled back.


def check_attempt(attempt: LoginAttempt):
    if attempt.failures >= 5:
        raise AppError(429, "login_limited", "验证尝试过多，请在 15 分钟窗口结束后重试。")


def confirm_password(
    session: Session, user: User, request: Request, password: str
) -> tuple[User, LoginAttempt]:
    # Preserve IDs before refreshing: current_user may have cached these ORM rows.
    user_id, username = user.id, user.username
    attempt = lock_attempt(session, username)
    locked = session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    token = request.cookies.get(COOKIE, "")
    expires_at = session.scalar(
        select(AuthSession.expires_at)
        .where(
            AuthSession.token_hash == token_hash(token),
            AuthSession.user_id == user_id,
            AuthSession.expires_at > datetime.now(UTC),
        )
        .with_for_update()
    )
    # Re-authorize after waiting: a concurrent password reset may have revoked us.
    if locked is None or expires_at is None or utc(expires_at) <= datetime.now(UTC):
        raise AppError(401, "authentication_required", "请登录后继续。")
    check_attempt(attempt)
    if not verify_password(password, locked.password_hash):
        attempt.failures += 1
        session.commit()
        raise AppError(403, "invalid_current_password", "当前密码错误。")
    attempt.failures, attempt.window_started = 0, datetime.now(UTC)
    return locked, attempt


def cleared_session_response(settings: Settings) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE, path="/api", secure=settings.session_cookie_secure, httponly=True, samesite="strict"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def current_user(request: Request, session: SessionDep) -> User:
    token = request.cookies.get(COOKIE, "")
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        saved = session.get(AuthSession, token_hash(token))
        if saved and utc(saved.expires_at) > datetime.now(UTC):
            user = session.get(User, saved.user_id)
            if user:
                request.state.user_id = user.id
                return user
    raise AppError(401, "authentication_required", "请登录后继续。")


UserDep = Annotated[User, Depends(current_user)]


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("username")
    @classmethod
    def username_valid(cls, value):
        return normalize_username(value)


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str


class PasswordConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128, repr=False)


class PasswordChange(PasswordConfirmation):
    new_password: str = Field(min_length=12, max_length=128, repr=False)


class SessionView(BaseModel):
    id: str
    created_at: datetime | None
    expires_at: datetime
    current: bool


@router.get("/me", response_model=UserView)
def me(user: UserDep):
    return user


@router.post("/login", response_model=UserView)
def login(
    body: Credentials,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
):
    attempt = lock_attempt(session, body.username)
    check_attempt(attempt)
    user = session.scalar(
        select(User)
        .where(User.username == body.username)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    dummy = f"scrypt-v1${'00' * 16}${'00' * 64}"
    valid = verify_password(body.password, user.password_hash if user else dummy)
    if not valid or user is None:
        attempt.failures += 1
        session.commit()
        raise AppError(401, "invalid_credentials", "用户名或密码错误。")
    attempt.failures = 0
    now = datetime.now(UTC)
    old = request.cookies.get(COOKIE)
    if old:
        session.execute(
            delete(AuthSession).where(
                AuthSession.token_hash == token_hash(old), AuthSession.user_id == user.id
            )
        )
    token = secrets.token_urlsafe(32)
    # Keep at most 10 sessions per user, including this new session.
    previous = session.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == user.id)
        .order_by(AuthSession.expires_at.desc())
    ).all()
    for saved in previous[9:]:
        session.delete(saved)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=user.id,
            expires_at=now + timedelta(hours=settings.session_hours),
        )
    )
    session.commit()
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/api",
    )
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, session: SessionDep, settings: SettingsDep):
    token = request.cookies.get(COOKIE, "")
    if token:
        session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
        session.commit()
    return cleared_session_response(settings)


@router.get("/sessions", response_model=list[SessionView])
def sessions(user: UserDep, request: Request, session: SessionDep):
    current_hash = token_hash(request.cookies.get(COOKIE, ""))
    saved = session.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.expires_at > datetime.now(UTC))
        .order_by(
            (AuthSession.token_hash == current_hash).desc(),
            AuthSession.expires_at.desc(),
            AuthSession.id,
        )
        .limit(10)
    ).all()
    return [
        SessionView(
            id=item.id,
            created_at=utc(item.created_at) if item.created_at else None,
            expires_at=utc(item.expires_at),
            current=item.token_hash == current_hash,
        )
        for item in saved
    ]


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChange,
    user: UserDep,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
):
    locked, _ = confirm_password(session, user, request, body.current_password)
    if body.new_password == body.current_password:
        raise AppError(422, "password_unchanged", "新密码须与当前密码不同。")
    locked.password_hash = hash_password(body.new_password)
    count = session.execute(delete(AuthSession).where(AuthSession.user_id == locked.id)).rowcount
    session.add(
        AuditEvent(
            event="password_changed", details={"user_id": locked.id, "revoked_sessions": count}
        )
    )
    session.commit()
    return cleared_session_response(settings)


@router.post("/logout-all", status_code=204)
def logout_all(
    body: PasswordConfirmation,
    user: UserDep,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
):
    locked, _ = confirm_password(session, user, request, body.current_password)
    count = session.execute(delete(AuthSession).where(AuthSession.user_id == locked.id)).rowcount
    session.add(
        AuditEvent(
            event="sessions_revoked", details={"user_id": locked.id, "revoked_sessions": count}
        )
    )
    session.commit()
    return cleared_session_response(settings)


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: UUID,
    body: PasswordConfirmation,
    user: UserDep,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
):
    target = select(AuthSession).where(
        AuthSession.id == str(session_id),
        AuthSession.user_id == user.id,
        AuthSession.expires_at > datetime.now(UTC),
    )
    if session.scalar(target) is None:
        raise AppError(404, "session_not_found", "会话不存在或已过期。")
    locked, _ = confirm_password(session, user, request, body.current_password)
    # Re-read after acquiring credential locks, including a fresh expiry cutoff.
    saved = session.scalar(
        select(AuthSession)
        .where(
            AuthSession.id == str(session_id),
            AuthSession.user_id == locked.id,
            AuthSession.expires_at > datetime.now(UTC),
        )
        .execution_options(populate_existing=True)
    )
    if saved is None:
        raise AppError(404, "session_not_found", "会话不存在或已过期。")
    is_current = saved.token_hash == token_hash(request.cookies.get(COOKIE, ""))
    session.delete(saved)
    session.add(
        AuditEvent(
            event="session_revoked", details={"user_id": locked.id, "session_id": str(session_id)}
        )
    )
    session.commit()
    return cleared_session_response(settings) if is_current else Response(status_code=204)
