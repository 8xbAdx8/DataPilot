import secrets
from datetime import UTC, datetime, timedelta
from threading import BoundedSemaphore

import pytest
from sqlalchemy import select

from app.accounts import assign_legacy, create_user
from app.auth import COOKIE, hash_password, token_hash, verify_password
from app.errors import AppError
from app.maintenance import cleanup
from app.models import AuthSession, Dataset, LoginAttempt, User

PASSWORD = "Synthetic-test-password-2026!"


@pytest.fixture(scope="module")
def encoded():
    return hash_password(PASSWORD)


@pytest.fixture
def login_user(auth_user, session, encoded):
    auth_user.password_hash = encoded
    session.commit()
    return auth_user


def login(client, user, password=PASSWORD):
    return client.post("/api/auth/login", json={"username": user.username, "password": password})


def test_scrypt_uses_salt_and_constant_hash_version(encoded):
    another = hash_password(PASSWORD)
    assert encoded != another
    assert verify_password(PASSWORD, encoded)
    assert not verify_password("incorrect", encoded)
    assert not verify_password(PASSWORD, "malformed")
    for password in ("short", "x" * 129):
        with pytest.raises(ValueError):
            hash_password(password)


def test_real_login_cookie_hash_me_and_logout_revocation(client, session, login_user):
    client.cookies.clear()
    response = login(client, login_user)
    assert response.status_code == 200
    assert response.json() == {"id": login_user.id, "username": login_user.username}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
    token = client.cookies.get(COOKIE)
    saved = session.get(AuthSession, token_hash(token))
    assert saved.user_id == login_user.id and saved.token_hash != token
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/auth/me").json()["id"] == login_user.id
    assert client.post("/api/auth/logout").status_code == 204
    assert session.get(AuthSession, token_hash(token)) is None
    client.cookies.clear()
    client.cookies.set(COOKIE, token)
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 204


def test_login_rotates_supplied_session_and_secure_cookie(client, session, settings, login_user):
    old = client.cookies.get(COOKIE)
    client.cookies.clear()
    client.cookies.set(COOKIE, old, domain="testserver.local", path="/api")
    settings.session_cookie_secure = True
    response = login(client, login_user)
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
    assert session.get(AuthSession, token_hash(old)) is None


def test_expired_and_forged_sessions_are_rejected(client, session):
    token = client.cookies.get(COOKIE)
    saved = session.get(AuthSession, token_hash(token))
    saved.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert client.get("/api/auth/me").status_code == 401
    for value in (secrets.token_urlsafe(32), "../../token", "x" * 1000):
        client.cookies.clear()
        client.cookies.set(COOKIE, value)
        assert client.get("/api/datasets").status_code == 401


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/datasets"),
        ("POST", "/api/datasets"),
        ("GET", "/api/datasets/00000000-0000-0000-0000-000000000001"),
        ("DELETE", "/api/datasets/00000000-0000-0000-0000-000000000001"),
        ("POST", "/api/datasets/00000000-0000-0000-0000-000000000001/analyses"),
        ("GET", "/api/analyses"),
        ("GET", "/api/analyses/00000000-0000-0000-0000-000000000001"),
        ("GET", "/api/analyses/00000000-0000-0000-0000-000000000001/export"),
    ],
)
def test_every_data_route_requires_authentication(client, method, path):
    client.cookies.clear()
    assert client.request(method, path).status_code == 401
    assert client.get("/api/health/live").status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://attacker.invalid"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_site_mutations_are_rejected_even_with_cookie_and_header(client, headers):
    response = client.post("/api/auth/logout", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_rejected"
    assert client.get("/api/auth/me").status_code == 200


def test_custom_header_is_required_for_login_and_authenticated_mutations(client):
    del client.headers["X-DataPilot-CSRF"]
    assert client.post("/api/auth/login", json={}).status_code == 403
    assert client.post("/api/datasets").status_code == 403
    assert client.post("/api/auth/logout").status_code == 403


def test_password_is_not_echoed_in_validation_error(client):
    password = "private-value-" * 30
    response = client.post("/api/auth/login", json={"username": "someone", "password": password})
    assert response.status_code == 422
    assert "private-value-" not in response.text
    assert "input" not in response.text


def test_generic_credentials_error_and_persistent_login_limit(client, login_user, session):
    client.cookies.clear()
    for _ in range(5):
        response = login(client, login_user, "incorrect")
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "invalid_credentials"
    assert login(client, login_user).status_code == 429
    unknown = client.post(
        "/api/auth/login",
        json={"username": "unknown-" + secrets.token_hex(8), "password": "incorrect"},
    )
    assert unknown.json() == response.json()
    attempt = session.get(LoginAttempt, token_hash(login_user.username))
    attempt.window_started = datetime.now(UTC) - timedelta(minutes=16)
    session.commit()
    assert login(client, login_user).status_code == 200


def test_password_worker_slots_are_bounded(monkeypatch, encoded):
    slots = BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr("app.auth.PASSWORD_SLOTS", slots)
    with pytest.raises(AppError) as failure:
        verify_password(PASSWORD, encoded)
    assert failure.value.status_code == 503


def test_successful_login_caps_outstanding_sessions(client, session, login_user):
    for index in range(12):
        session.add(
            AuthSession(
                token_hash=token_hash(f"old-session-{index}"),
                user_id=login_user.id,
                expires_at=datetime.now(UTC) + timedelta(minutes=index + 1),
            )
        )
    session.commit()
    assert login(client, login_user).status_code == 200
    saved = session.scalars(select(AuthSession).where(AuthSession.user_id == login_user.id)).all()
    assert len(saved) == 10


def test_ownership_enforced_for_every_dataset_analysis_and_export_route(
    client, session, monkeypatch
):
    uploaded = client.post(
        "/api/datasets", files={"file": ("private.csv", b"region,revenue\nEast,10\n")}
    ).json()
    dataset_id = uploaded["id"]
    analysis = client.post(f"/api/datasets/{dataset_id}/analyses", json={"question": "行数"}).json()
    original = client.cookies.get(COOKIE)
    other = User(username="other-" + secrets.token_hex(8), password_hash="unused-test")
    session.add(other)
    session.flush()
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=other.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    session.commit()
    client.cookies.clear()
    client.cookies.set(COOKIE, token)
    monkeypatch.setattr(
        "app.analyses.execute_query", lambda *_: pytest.fail("Foreign data executed")
    )
    for method, path in [
        ("GET", f"/api/datasets/{dataset_id}"),
        ("DELETE", f"/api/datasets/{dataset_id}"),
        ("POST", f"/api/datasets/{dataset_id}/analyses"),
        ("GET", f"/api/analyses/{analysis['id']}"),
        ("GET", f"/api/analyses/{analysis['id']}/export"),
    ]:
        assert (
            client.request(
                method, path, json={"question": "行数"} if method == "POST" else None
            ).status_code
            == 404
        )
    assert client.get("/api/datasets").json() == []
    assert client.get("/api/analyses", params={"dataset_id": dataset_id, "q": "COUNT"}).json() == []
    assert client.get("/api/analyses").json() == []
    owned = client.post(
        "/api/datasets",
        data={"owner_id": "forged-owner-id"},
        files={"file": ("other.csv", b"a,b\n1,2\n")},
    ).json()
    assert session.get(Dataset, owned["id"]).owner_id == other.id
    client.cookies.clear()
    client.cookies.set(COOKIE, original)
    assert client.get(f"/api/datasets/{owned['id']}").status_code == 404
    assert client.get(f"/api/datasets/{dataset_id}").status_code == 200


def test_legacy_uploads_hidden_until_explicit_operator_assignment(client, session, auth_user):
    uploaded = client.post("/api/datasets", files={"file": ("old.csv", b"a,b\n1,2\n")}).json()
    saved = session.get(Dataset, uploaded["id"])
    saved.owner_id = None
    session.commit()
    assert client.get(f"/api/datasets/{saved.id}").status_code == 404
    assert client.get("/api/datasets").json() == []
    assert assign_legacy(session, auth_user.username) >= 1
    session.refresh(saved)
    assert saved.owner_id is None
    assign_legacy(session, auth_user.username, apply=True)
    session.refresh(saved)
    assert saved.owner_id == auth_user.id
    assert client.get(f"/api/datasets/{saved.id}").status_code == 200


def test_operator_provisioning_does_not_overwrite_existing_account(session, encoded):
    user = create_user(session, "Provision-" + secrets.token_hex(8), PASSWORD)
    assert user.username == user.username.lower()
    with pytest.raises(ValueError):
        create_user(session, user.username.upper(), "different-password-123")
    assert verify_password(PASSWORD, user.password_hash)


def test_retention_removes_expired_sessions_and_attempts_only_on_apply(
    client, session, settings, auth_user
):
    old_hash = token_hash("expired-test-token")
    attempt_key = token_hash("expired-test-attempt")
    session.add(
        AuthSession(
            token_hash=old_hash,
            user_id=auth_user.id,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    session.add(
        LoginAttempt(
            key=attempt_key, failures=5, window_started=datetime.now(UTC) - timedelta(minutes=20)
        )
    )
    session.commit()
    assert cleanup(session, settings)["sessions"] == 1
    assert session.get(AuthSession, old_hash)
    counts = cleanup(session, settings, apply=True)
    assert counts["sessions"] == 1 and counts["login_attempts"] == 1
    assert session.get(AuthSession, old_hash) is None
    assert session.get(LoginAttempt, attempt_key) is None
    assert client.get("/api/auth/me").status_code == 200
