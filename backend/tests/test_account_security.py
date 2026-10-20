"""Password changes and session revocation use real cookies, hashes and persistence."""

import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import accounts
from app.auth import COOKIE, hash_password, token_hash, verify_password
from app.models import AuditEvent, AuthSession, LoginAttempt, User

PASSWORD = "Account-security-original-2026!"
NEW_PASSWORD = "Account-security-replacement-2026!"


@pytest.fixture(scope="module")
def encoded_password():
    return hash_password(PASSWORD)


@pytest.fixture
def account(auth_user, session, encoded_password):
    auth_user.password_hash = encoded_password
    session.commit()
    return auth_user


def add_session(session, user, *, expired=False):
    token = secrets.token_urlsafe(32)
    saved = AuthSession(
        token_hash=token_hash(token),
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=-1 if expired else 2),
    )
    session.add(saved)
    session.commit()
    return token, saved


def other_account(session, encoded_password):
    user = User(username="other-security-" + secrets.token_hex(8), password_hash=encoded_password)
    session.add(user)
    session.commit()
    token, saved = add_session(session, user)
    return user, token, saved


def use_cookie(client, token):
    client.cookies.clear()
    client.cookies.set(COOKIE, token)


def session_hashes(session, user):
    return set(
        session.scalars(select(AuthSession.token_hash).where(AuthSession.user_id == user.id))
    )


def mutation(client, action, current_password=PASSWORD, **extra):
    body = {"current_password": current_password, **extra}
    if action == "password":
        body.setdefault("new_password", NEW_PASSWORD)
    method = "DELETE" if action.startswith("sessions/") else "POST"
    return client.request(method, f"/api/auth/{action}", json=body)


def assert_cookie_cleared(response):
    assert response.status_code == 204 and not response.content
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "Path=/api" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


def assert_audit_has_no_secrets(session, *secrets_to_hide):
    rows = session.scalars(select(AuditEvent)).all()
    assert rows, "Security-sensitive changes must be audited"
    encoded = json.dumps([{"event": row.event, "details": row.details} for row in rows])
    for secret in secrets_to_hide:
        assert secret not in encoded


def test_session_inventory_is_private_bounded_and_never_exposes_tokens(
    client, session, account, encoded_password
):
    current_token = client.cookies.get(COOKIE)
    current = session.get(AuthSession, token_hash(current_token))
    tokens = [current_token]
    for _ in range(10):
        token, _ = add_session(session, account)
        tokens.append(token)
    _, expired = add_session(session, account, expired=True)
    _, other_token, other = other_account(session, encoded_password)
    response = client.get("/api/auth/sessions")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 10
    assert len({row["id"] for row in rows}) == len(rows)
    assert all(set(row) == {"id", "created_at", "expires_at", "current"} for row in rows)
    assert any(row["id"] == current.id and row["current"] for row in rows)
    assert sum(row["current"] for row in rows) == 1
    for row in rows:
        assert str(uuid.UUID(row["id"])) == row["id"]
        assert row["id"] not in {expired.id, other.id}
        assert datetime.fromisoformat(row["expires_at"]).replace(tzinfo=UTC) > datetime.now(UTC)
        if row["created_at"] is not None:
            datetime.fromisoformat(row["created_at"])
    for token in [*tokens, other_token]:
        assert token not in response.text and token_hash(token) not in response.text


def test_legacy_session_creation_time_remains_unknown(client, session, account):
    token, legacy = add_session(session, account)
    legacy.created_at = None
    session.commit()
    rows = client.get("/api/auth/sessions").json()
    assert next(row for row in rows if row["id"] == legacy.id)["created_at"] is None
    assert token not in json.dumps(rows)


def test_revoke_other_own_session_preserves_current_and_other_user(
    client, session, account, encoded_password
):
    current_token = client.cookies.get(COOKIE)
    target_token, target = add_session(session, account)
    _, other_token, other = other_account(session, encoded_password)
    response = mutation(client, f"sessions/{target.id}")
    assert response.status_code == 204 and not response.content
    assert "set-cookie" not in response.headers
    assert session.get(AuthSession, token_hash(target_token)) is None
    assert session.get(AuthSession, token_hash(current_token)) is not None
    assert session.get(AuthSession, other.token_hash) is not None
    assert client.get("/api/auth/me").status_code == 200
    use_cookie(client, target_token)
    assert client.get("/api/auth/me").status_code == 401
    use_cookie(client, other_token)
    assert client.get("/api/auth/me").status_code == 200
    assert_audit_has_no_secrets(session, PASSWORD, current_token, target_token, other_token)


def test_foreign_and_unknown_session_ids_do_not_reveal_or_delete_foreign_session(
    client, session, account, encoded_password
):
    _, _, other = other_account(session, encoded_password)
    unknown_id = str(uuid.uuid4())
    foreign = mutation(client, f"sessions/{other.id}")
    missing = mutation(client, f"sessions/{unknown_id}")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert session.get(AuthSession, other.token_hash) is not None
    assert client.get("/api/auth/me").status_code == 200


def test_revoking_current_session_clears_cookie_but_retains_other_sessions(
    client, session, account
):
    token = client.cookies.get(COOKIE)
    current = session.get(AuthSession, token_hash(token))
    second_token, second = add_session(session, account)
    response = mutation(client, f"sessions/{current.id}")
    assert_cookie_cleared(response)
    assert session.get(AuthSession, token_hash(token)) is None
    assert session.get(AuthSession, second.token_hash) is not None
    use_cookie(client, token)
    assert client.get("/api/auth/me").status_code == 401
    use_cookie(client, second_token)
    assert client.get("/api/auth/me").status_code == 200


def test_logout_all_revokes_all_own_sessions_including_expired_only(
    client, session, account, encoded_password
):
    tokens = [client.cookies.get(COOKIE), add_session(session, account)[0]]
    add_session(session, account, expired=True)
    _, other_token, other = other_account(session, encoded_password)
    response = mutation(client, "logout-all")
    assert_cookie_cleared(response)
    assert session_hashes(session, account) == set()
    assert session.get(AuthSession, other.token_hash) is not None
    for token in tokens:
        use_cookie(client, token)
        assert client.get("/api/auth/me").status_code == 401
    use_cookie(client, other_token)
    assert client.get("/api/auth/me").status_code == 200
    assert_audit_has_no_secrets(session, PASSWORD, *tokens, other_token)


def test_password_change_updates_real_hash_and_revokes_every_own_session(
    client, session, account, encoded_password
):
    original_hash = account.password_hash
    tokens = [client.cookies.get(COOKIE), add_session(session, account)[0]]
    _, other_token, other = other_account(session, encoded_password)
    response = mutation(client, "password")
    assert_cookie_cleared(response)
    session.refresh(account)
    assert account.password_hash != original_hash
    assert verify_password(NEW_PASSWORD, account.password_hash)
    assert not verify_password(PASSWORD, account.password_hash)
    assert session_hashes(session, account) == set()
    assert session.get(AuthSession, other.token_hash) is not None
    for token in tokens:
        use_cookie(client, token)
        assert client.get("/api/auth/me").status_code == 401
    use_cookie(client, other_token)
    assert client.get("/api/auth/me").status_code == 200
    client.cookies.clear()
    assert (
        client.post(
            "/api/auth/login", json={"username": account.username, "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login", json={"username": account.username, "password": NEW_PASSWORD}
        ).status_code
        == 200
    )
    assert_audit_has_no_secrets(session, PASSWORD, NEW_PASSWORD, *tokens, other_token)


@pytest.mark.parametrize("new_password", ["a" * 12, "a" * 128])
def test_password_change_accepts_both_password_length_boundaries(
    client, session, account, new_password
):
    assert_cookie_cleared(mutation(client, "password", new_password=new_password))
    session.refresh(account)
    assert verify_password(new_password, account.password_hash)


@pytest.mark.parametrize("action", ["password", "logout-all", "sessions/current"])
def test_incorrect_current_password_never_changes_hash_or_sessions(
    client, session, account, action, caplog
):
    caplog.set_level(logging.INFO, logger="datapilot")
    current = session.get(AuthSession, token_hash(client.cookies.get(COOKIE)))
    add_session(session, account)
    before = session_hashes(session, account)
    original_hash = account.password_hash
    if action == "sessions/current":
        action = f"sessions/{current.id}"
    private_password = "Private-incorrect-password-2026!"
    response = mutation(client, action, private_password)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "invalid_current_password"
    session.refresh(account)
    assert account.password_hash == original_hash
    assert session_hashes(session, account) == before
    assert client.get("/api/auth/me").status_code == 200
    assert private_password not in response.text + caplog.text
    assert NEW_PASSWORD not in response.text + caplog.text


def test_confirmation_failures_share_login_window_and_expire_after_fifteen_minutes(
    client, session, account
):
    for _ in range(4):
        assert mutation(client, "logout-all", "incorrect").status_code == 403
    assert (
        client.post(
            "/api/auth/login", json={"username": account.username, "password": "incorrect"}
        ).status_code
        == 401
    )
    limited = mutation(client, "password")
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "login_limited"
    assert (
        client.post(
            "/api/auth/login", json={"username": account.username, "password": PASSWORD}
        ).status_code
        == 429
    )
    attempt = session.get(LoginAttempt, token_hash(account.username))
    assert attempt.failures == 5
    attempt.window_started = datetime.now(UTC) - timedelta(minutes=16)
    session.commit()
    assert_cookie_cleared(mutation(client, "logout-all"))


def test_successful_password_confirmation_clears_prior_failure_count(client, session, account):
    _, target = add_session(session, account)
    assert mutation(client, "logout-all", "incorrect").status_code == 403
    assert mutation(client, f"sessions/{target.id}").status_code == 204
    attempt = session.get(LoginAttempt, token_hash(account.username))
    assert attempt is None or attempt.failures == 0


def test_password_must_change_without_revoking_existing_session(client, session, account):
    before = session_hashes(session, account)
    response = mutation(client, "password", new_password=PASSWORD)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "password_unchanged"
    assert session_hashes(session, account) == before
    assert client.get("/api/auth/me").status_code == 200


@pytest.mark.parametrize(
    "action,body",
    [
        ("password", {"current_password": PASSWORD, "new_password": "short"}),
        ("password", {"current_password": PASSWORD, "new_password": "private-sentinel" * 10}),
        (
            "password",
            {
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "extra": "private-sentinel",
            },
        ),
        ("password", {"new_password": NEW_PASSWORD}),
        ("logout-all", {"current_password": PASSWORD, "extra": "private-sentinel"}),
        ("sessions/current", {"current_password": PASSWORD, "extra": "private-sentinel"}),
        ("logout-all", {}),
    ],
)
def test_password_request_validation_rejects_extra_fields_and_never_echoes_inputs(
    client, session, account, action, body, caplog
):
    current = session.get(AuthSession, token_hash(client.cookies.get(COOKIE)))
    if action == "sessions/current":
        action = f"sessions/{current.id}"
    caplog.set_level(logging.INFO, logger="datapilot")
    before = session_hashes(session, account)
    method = "DELETE" if action.startswith("sessions/") else "POST"
    response = client.request(method, f"/api/auth/{action}", json=body)
    assert response.status_code == 422
    assert all("input" not in error for error in response.json()["detail"])
    assert session_hashes(session, account) == before
    for value in (PASSWORD, NEW_PASSWORD, "private-sentinel"):
        assert value not in response.text + caplog.text


@pytest.mark.parametrize("action", ["password", "logout-all", "sessions/current"])
@pytest.mark.parametrize("cross_site", [False, True])
def test_security_mutations_require_csrf_and_trusted_origin(
    client, session, account, action, cross_site
):
    current = session.get(AuthSession, token_hash(client.cookies.get(COOKIE)))
    if action == "sessions/current":
        action = f"sessions/{current.id}"
    before = session_hashes(session, account)
    if cross_site:
        client.headers["Origin"] = "https://attacker.invalid"
    else:
        del client.headers["X-DataPilot-CSRF"]
    response = mutation(client, action)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_rejected"
    assert session_hashes(session, account) == before


@pytest.mark.parametrize("action", ["sessions", "password", "logout-all", "sessions/current"])
def test_account_security_endpoints_require_unexpired_current_session(
    client, session, account, action
):
    current = session.get(AuthSession, token_hash(client.cookies.get(COOKIE)))
    if action == "sessions/current":
        action = f"sessions/{current.id}"
    current.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    for token in (client.cookies.get(COOKIE), None):
        client.cookies.clear()
        if token:
            client.cookies.set(COOKIE, token)
        response = (
            client.get("/api/auth/sessions") if action == "sessions" else mutation(client, action)
        )
        assert response.status_code == 401


def test_operator_password_reset_revokes_sessions_and_resets_only_target_failures(
    client, session, account, encoded_password
):
    tokens = [client.cookies.get(COOKIE), add_session(session, account)[0]]
    other, other_token, other_saved = other_account(session, encoded_password)
    for user in (account, other):
        session.add(LoginAttempt(key=token_hash(user.username), failures=5))
    session.commit()
    accounts.reset_password(session, account.username.upper(), NEW_PASSWORD)
    session.refresh(account)
    assert verify_password(NEW_PASSWORD, account.password_hash)
    assert not verify_password(PASSWORD, account.password_hash)
    assert session_hashes(session, account) == set()
    attempt = session.get(LoginAttempt, token_hash(account.username))
    assert attempt is None or attempt.failures == 0
    assert session.get(LoginAttempt, token_hash(other.username)).failures == 5
    assert session.get(AuthSession, other_saved.token_hash) is not None
    for token in tokens:
        use_cookie(client, token)
        assert client.get("/api/auth/me").status_code == 401
    use_cookie(client, other_token)
    assert client.get("/api/auth/me").status_code == 200
    assert_audit_has_no_secrets(session, PASSWORD, NEW_PASSWORD, *tokens, other_token)


@pytest.mark.parametrize("password", ["short", "x" * 129])
def test_operator_rejects_weak_reset_without_any_changes(client, session, account, password):
    original_hash, before = account.password_hash, session_hashes(session, account)
    session.add(LoginAttempt(key=token_hash(account.username), failures=3))
    session.commit()
    with pytest.raises(ValueError):
        accounts.reset_password(session, account.username, password)
    session.refresh(account)
    assert account.password_hash == original_hash
    assert session_hashes(session, account) == before
    assert session.get(LoginAttempt, token_hash(account.username)).failures == 3
    assert session.scalars(select(AuditEvent)).all() == []


def test_operator_reset_of_unknown_account_does_not_create_user_or_audit(session):
    username = "missing-reset-" + secrets.token_hex(8)
    with pytest.raises(ValueError):
        accounts.reset_password(session, username, NEW_PASSWORD)
    assert session.scalar(select(User).where(User.username == username)) is None
    assert session.scalars(select(AuditEvent)).all() == []


def test_operator_reset_is_atomic_when_commit_fails(client, session, account, monkeypatch):
    original_hash, before = account.password_hash, session_hashes(session, account)
    session.add(LoginAttempt(key=token_hash(account.username), failures=4))
    session.commit()

    def fail_commit():
        raise SQLAlchemyError("Synthetic account-reset storage failure")

    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", fail_commit)
        with pytest.raises(SQLAlchemyError):
            accounts.reset_password(session, account.username, NEW_PASSWORD)
    session.expire_all()
    assert session.get(User, account.id).password_hash == original_hash
    assert session_hashes(session, account) == before
    assert session.get(LoginAttempt, token_hash(account.username)).failures == 4
    assert session.scalars(select(AuditEvent)).all() == []
