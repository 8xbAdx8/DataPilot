"""Check migrations in a newly created database; never downgrade the configured DB."""

import argparse
import json
import os
import secrets

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from app.config import Settings, get_settings
from app.db import get_engine
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError


def run(host=None):
    url = make_url(Settings().database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("PostgreSQL administration connection required")
    if host:
        url = url.set(host=host)
    admin = create_engine(
        url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5}
    )
    name = "datapilot_migration_" + secrets.token_hex(8)
    assert name.startswith("datapilot_migration_") and name.replace("_", "").isalnum()
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = True
        os.environ["DATABASE_URL"] = url.set(database=name).render_as_string(
            hide_password=False
        )
        get_settings.cache_clear()
        get_engine.cache_clear()
        config = Config("backend/alembic.ini")
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        command.check(config)
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        command.check(config)
        with get_engine().connect() as connection:
            assert (
                connection.scalar(text("select version_num from alembic_version"))
                == "0006"
            )
            assert connection.scalar(
                text("select extversion from pg_extension where extname='vector'")
            )
        return {
            "status": "passed",
            "upgrade_twice": True,
            "downgrade_base_reupgrade": True,
            "alembic_check": True,
            "head": "0006",
        }
    finally:
        get_engine().dispose()
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disposable", action="store_true")
    parser.add_argument("--host")
    args = parser.parse_args()
    if not args.disposable:
        parser.error("Explicit --disposable authorizes a new temporary database")
    try:
        print(json.dumps(run(args.host)))
    except (
        SQLAlchemyError,
        CommandError,
        OSError,
        AssertionError,
        ValueError,
    ) as error:
        print(json.dumps({"status": "failed", "failure_class": type(error).__name__}))
        raise SystemExit(1) from None
