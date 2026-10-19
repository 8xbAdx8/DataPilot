"""Opaque, revocable server sessions. No tokens or passwords in JSON responses."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from threading import BoundedSemaphore
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.errors import AppError
from app.models import AuthSession, LoginAttempt, User

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
    now = datetime.now(UTC)
    key = token_hash(body.username)
    # The persistent row serializes guesses for this account, including unknown names.
    attempt = session.scalar(select(LoginAttempt).where(LoginAttempt.key == key).with_for_update())
    if attempt is None:
        session.add(LoginAttempt(key=key, failures=0, window_started=now))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()  # A simultaneous first request created the same row.
        attempt = session.scalar(
            select(LoginAttempt).where(LoginAttempt.key == key).with_for_update()
        )
    if utc(attempt.window_started) <= now - timedelta(minutes=15):
        attempt.failures, attempt.window_started = 0, now
    if attempt.failures >= 5:
        raise AppError(429, "login_limited", "登录尝试过多，请在 15 分钟窗口结束后重试。")
    user = session.scalar(select(User).where(User.username == body.username))
    dummy = f"scrypt-v1${'00' * 16}${'00' * 64}"
    valid = verify_password(body.password, user.password_hash if user else dummy)
    if not valid or user is None:
        attempt.failures += 1
        session.commit()
        raise AppError(401, "invalid_credentials", "用户名或密码错误。")
    attempt.failures = 0
    old = request.cookies.get(COOKIE)
    if old:
        session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(old)))
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
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE, path="/api", secure=settings.session_cookie_secure, httponly=True, samesite="strict"
    )
    response.headers["Cache-Control"] = "no-store"
    return response
