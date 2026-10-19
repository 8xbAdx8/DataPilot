import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth import COOKIE, token_hash
from app.config import Settings, get_settings
from app.db import Base, get_session
from app.main import app
from app.models import AuthSession, User


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, max_dataset_rows=100, max_dataset_columns=10)


@pytest.fixture
def session(tmp_path):
    # Default tests isolate persistence in a temporary SQLite file; rerun with real PG explicitly.
    url = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.sqlite'}")
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            yield session
        transaction.rollback()
    engine.dispose()


@pytest.fixture
def auth_user(session):
    user = User(username="test-" + secrets.token_hex(8), password_hash="unused-in-session-fixture")
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def client(settings, session, auth_user):
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = lambda: session
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=auth_user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    session.commit()
    with TestClient(
        app, raise_server_exceptions=False, headers={"X-DataPilot-CSRF": "1"}
    ) as client:
        client.cookies.set(COOKIE, token)
        yield client
    app.dependency_overrides.clear()
