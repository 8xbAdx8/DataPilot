import os

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.postgres
def test_postgres_migrations_and_connection():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL to explicitly test a migrated PostgreSQL database")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert "PostgreSQL" in connection.scalar(text("SELECT version()"))
            assert connection.scalar(text("SELECT version_num FROM alembic_version"))
            connection.execute(text("SELECT id, profile, owner_id FROM datasets LIMIT 0"))
            connection.execute(
                text("SELECT token_hash, user_id, expires_at FROM auth_sessions LIMIT 0")
            )
    finally:
        engine.dispose()
