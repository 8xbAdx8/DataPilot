"""Real PostgreSQL transactions prove credential changes serialize across workers.

These tests deliberately keep ORM instances cached, as production requests use
expire_on_commit=False. SQLite cannot exercise these row-lock guarantees.
"""

import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from queue import Queue
from threading import Event
from time import monotonic

import pytest
from fastapi import Request, Response
from sqlalchemy import Engine, create_engine, delete, select, text
from sqlalchemy.orm import Session

from app import accounts, auth
from app.config import Settings
from app.errors import AppError
from app.models import AuditEvent, AuthSession, LoginAttempt, User

pytestmark = pytest.mark.postgres
PASSWORD = "Concurrent-original-password-2026!"
NEW_PASSWORD = "Concurrent-replacement-password-2026!"
OTHER_PASSWORD = "Concurrent-other-replacement-2026!"
_real_hash_password = auth.hash_password


@lru_cache(maxsize=3)
def encoded(password):
    # Real scrypt, cached only for these synthetic fixtures; never a fake hash.
    return _real_hash_password(password)


@dataclass(frozen=True)
class Account:
    engine: Engine
    user_id: str
    username: str
    token: str


@pytest.fixture
def pg_account(monkeypatch):
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL for real PostgreSQL credential concurrency")
    engine = create_engine(
        url,
        # A failed assertion must not leave a worker waiting forever on a row lock.
        connect_args={"options": "-c lock_timeout=10000 -c statement_timeout=15000"},
    )
    for password in (PASSWORD, NEW_PASSWORD, OTHER_PASSWORD):
        encoded(password)
    monkeypatch.setattr(auth, "hash_password", encoded)
    monkeypatch.setattr(accounts, "hash_password", encoded)
    token = secrets.token_urlsafe(32)
    with Session(engine) as session:
        user = User(username="auth-race-" + secrets.token_hex(8), password_hash=encoded(PASSWORD))
        session.add(user)
        session.flush()
        account = Account(engine, user.id, user.username, token)
        session.add_all(
            [
                LoginAttempt(key=auth.token_hash(user.username), failures=0),
                AuthSession(
                    token_hash=auth.token_hash(token),
                    user_id=user.id,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            ]
        )
        session.commit()
    try:
        yield account
    finally:
        with Session(engine) as session:
            # Only records created by this fixture, including its successful audits.
            session.execute(delete(AuthSession).where(AuthSession.user_id == account.user_id))
            session.execute(
                delete(LoginAttempt).where(LoginAttempt.key == auth.token_hash(account.username))
            )
            session.execute(
                delete(AuditEvent).where(
                    AuditEvent.details["user_id"].as_string() == account.user_id
                )
            )
            session.execute(delete(User).where(User.id == account.user_id))
            session.commit()
        engine.dispose()


def request_for(token=None):
    headers = [(b"cookie", f"{auth.COOKIE}={token}".encode())] if token else []
    return Request({"type": "http", "method": "POST", "path": "/api/auth", "headers": headers})


def worker_pid(session, ready):
    pid = session.scalar(text("SELECT pg_backend_pid()"))
    ready.put(pid)
    return pid


def wait_for_locks(engine, pids, blocker_pid):
    """Observe actual database lock waits, rather than guessing with a fixed sleep."""
    deadline = monotonic() + 8
    last = {}
    with engine.connect() as observer:
        while monotonic() < deadline:
            last = {
                pid: observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid})
                for pid in pids
            }
            if all(last.values()) and any(blocker_pid in blockers for blockers in last.values()):
                return
            Event().wait(0.02)  # Bounded polling of a concrete PostgreSQL condition.
    pytest.fail(f"Workers did not reach the expected PostgreSQL lock queue: {last}")


def login_worker(account, ready):
    with Session(account.engine, expire_on_commit=False) as session:
        worker_pid(session, ready)
        try:
            auth.login(
                auth.Credentials(username=account.username, password=PASSWORD),
                request_for(),
                Response(),
                session,
                Settings(_env_file=None),
            )
            return 200
        except AppError as error:
            session.rollback()
            return error.status_code


def reset_worker(account, ready):
    with Session(account.engine, expire_on_commit=False) as session:
        worker_pid(session, ready)
        return accounts.reset_password(session, account.username, NEW_PASSWORD).id


@pytest.mark.parametrize("first", ["reset", "login"])
def test_reset_and_old_password_login_serialize_in_both_orders(pg_account, first):
    account = pg_account
    ready = Queue()
    with (
        Session(account.engine) as blocker,
        ThreadPoolExecutor(max_workers=2) as workers,
    ):
        blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
        blocker.scalar(select(User).where(User.id == account.user_id).with_for_update())
        functions = {"reset": reset_worker, "login": login_worker}
        second = "login" if first == "reset" else "reset"
        try:
            first_result = workers.submit(functions[first], account, ready)
            first_pid = ready.get(timeout=8)
            # The first operation holds the attempt lock and waits for the user.
            wait_for_locks(account.engine, [first_pid], blocker_pid)
            second_result = workers.submit(functions[second], account, ready)
            second_pid = ready.get(timeout=8)
            wait_for_locks(account.engine, [second_pid], first_pid)
        finally:
            blocker.rollback()
        outcomes = {
            first: first_result.result(timeout=20),
            second: second_result.result(timeout=20),
        }
    assert outcomes["reset"] == account.user_id
    assert outcomes["login"] == (401 if first == "reset" else 200)
    with Session(account.engine) as session:
        assert session.get(User, account.user_id).password_hash == encoded(NEW_PASSWORD)
        assert (
            session.scalars(select(AuthSession).where(AuthSession.user_id == account.user_id)).all()
            == []
        )


def test_cached_user_and_revoked_session_cannot_authorize_after_reset(pg_account):
    account = pg_account
    request = request_for(account.token)
    with Session(account.engine, expire_on_commit=False) as stale:
        cached_user = auth.current_user(request, stale)
        cached_session = stale.get(AuthSession, auth.token_hash(account.token))
        stale.commit()
        with Session(account.engine) as operator:
            accounts.reset_password(operator, account.username, NEW_PASSWORD)
        # Prove the stale identity-map state exists before exercising the safeguard.
        assert stale.get(User, account.user_id) is cached_user
        assert stale.get(AuthSession, auth.token_hash(account.token)) is cached_session
        assert cached_user.password_hash == encoded(PASSWORD)
        with pytest.raises(AppError) as failure:
            auth.confirm_password(stale, cached_user, request, PASSWORD)
        assert failure.value.status_code == 401
        assert failure.value.detail["code"] == "authentication_required"
        assert cached_user.password_hash == encoded(NEW_PASSWORD)
        stale.rollback()


def test_cached_password_is_refreshed_even_with_a_new_valid_session(pg_account):
    account = pg_account
    fresh_token = secrets.token_urlsafe(32)
    with Session(account.engine, expire_on_commit=False) as stale:
        cached_user = stale.get(User, account.user_id)
        stale.commit()
        with Session(account.engine) as operator:
            accounts.reset_password(operator, account.username, NEW_PASSWORD)
            operator.add(
                AuthSession(
                    token_hash=auth.token_hash(fresh_token),
                    user_id=account.user_id,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            operator.commit()
        assert cached_user.password_hash == encoded(PASSWORD)
        with pytest.raises(AppError) as failure:
            auth.confirm_password(stale, cached_user, request_for(fresh_token), PASSWORD)
        assert failure.value.status_code == 403
        assert failure.value.detail["code"] == "invalid_current_password"
        assert cached_user.password_hash == encoded(NEW_PASSWORD)


def test_two_queued_password_changes_allow_exactly_one_success(pg_account):
    account = pg_account
    second_token = secrets.token_urlsafe(32)
    with Session(account.engine) as session:
        session.add(
            AuthSession(
                token_hash=auth.token_hash(second_token),
                user_id=account.user_id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session.commit()
    ready, start = Queue(), Event()

    def change(token, new_password):
        with Session(account.engine, expire_on_commit=False) as session:
            request = request_for(token)
            user = auth.current_user(request, session)
            worker_pid(session, ready)
            if not start.wait(timeout=10):
                raise AssertionError("Password-change workers were not released")
            try:
                response = auth.change_password(
                    auth.PasswordChange(current_password=PASSWORD, new_password=new_password),
                    user,
                    request,
                    session,
                    Settings(_env_file=None),
                )
                return response.status_code, new_password
            except AppError as error:
                session.rollback()
                return error.status_code, new_password

    with (
        Session(account.engine) as blocker,
        ThreadPoolExecutor(max_workers=2) as workers,
    ):
        blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
        blocker.scalar(select(User).where(User.id == account.user_id).with_for_update())
        futures = [
            workers.submit(change, account.token, NEW_PASSWORD),
            workers.submit(change, second_token, OTHER_PASSWORD),
        ]
        try:
            pids = [ready.get(timeout=8), ready.get(timeout=8)]
            # Both requests have authenticated and cached the original credentials.
            start.set()
            wait_for_locks(account.engine, pids, blocker_pid)
        finally:
            start.set()
            blocker.rollback()
        outcomes = [future.result(timeout=20) for future in futures]
    assert sorted(status for status, _ in outcomes) == [204, 401]
    winning_password = next(password for status, password in outcomes if status == 204)
    with Session(account.engine) as session:
        assert session.get(User, account.user_id).password_hash == encoded(winning_password)
        assert (
            session.scalars(select(AuthSession).where(AuthSession.user_id == account.user_id)).all()
            == []
        )
        audits = session.scalars(
            select(AuditEvent).where(AuditEvent.details["user_id"].as_string() == account.user_id)
        ).all()
        assert [event.event for event in audits] == ["password_changed"]
