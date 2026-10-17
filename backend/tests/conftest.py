import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import Base, get_session
from app.main import app


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
def client(settings, session):
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()
