import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError
from app.main import app
from app.maintenance import cleanup
from app.models import Analysis, Dataset, RateLimitBucket, User
from app.providers import SqlProposal, get_provider
from app.rate_limits import consume


def test_analysis_limit_rejects_before_provider_or_history_creation(
    client, settings, session, caplog
):
    settings.analysis_requests_per_window = 1

    class Provider:
        calls = 0

        def generate(self, *_):
            self.calls += 1
            return SqlProposal(sql="SELECT COUNT(*) FROM data", explanation="Count")

    provider = Provider()
    app.dependency_overrides[get_provider] = lambda: provider
    dataset = client.post("/api/datasets", files={"file": ("a.csv", b"a,b\n1,2\n")}).json()
    path = f"/api/datasets/{dataset['id']}/analyses"
    assert client.post(path, json={"question": "count"}).status_code == 201
    response = client.post(path, json={"question": "count"})
    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert response.json()["detail"]["retry_after"] == int(response.headers["retry-after"])
    assert provider.calls == 1
    records = [
        json.loads(record.getMessage()) for record in caplog.records if record.name == "datapilot"
    ]
    assert len([record for record in records if record["event"] == "analysis_completed"]) == 1
    assert records[-1]["status_code"] == 429
    assert (
        session.scalar(
            select(func.count()).select_from(Analysis).where(Analysis.dataset_id == dataset["id"])
        )
        == 1
    )


def test_admission_storage_failure_fails_closed_without_parsing_upload(client, monkeypatch, caplog):
    from sqlalchemy.exc import SQLAlchemyError

    def unavailable(*_):
        raise SQLAlchemyError("private-sentinel connection information")

    monkeypatch.setattr("app.datasets.consume", unavailable)
    monkeypatch.setattr("app.datasets.import_dataset", lambda *_: pytest.fail("Admission bypassed"))
    response = client.post("/api/datasets", files={"file": ("a.csv", b"a,b\n1,2\n")})
    assert response.status_code == 503 and response.headers["x-request-id"]
    assert "private-sentinel" not in response.text + caplog.text


def test_invalid_import_consumes_upload_quota_and_blocked_upload_never_parses(
    client, settings, session, monkeypatch, auth_user
):
    settings.upload_requests_per_window = 1
    assert client.post("/api/datasets", files={"file": ("a.csv", b"a,a\n1,2\n")}).status_code == 422
    monkeypatch.setattr(
        "app.datasets.import_dataset", lambda *_: pytest.fail("Blocked upload parsed")
    )
    response = client.post("/api/datasets", files={"file": ("ok.csv", b"a,b\n1,2\n")})
    assert response.status_code == 429
    assert (
        session.scalar(
            select(func.count()).select_from(Dataset).where(Dataset.owner_id == auth_user.id)
        )
        == 0
    )


def test_quota_is_independent_per_user_and_action_and_resets_at_expiry(
    session, settings, auth_user
):
    settings.analysis_requests_per_window = settings.upload_requests_per_window = 1
    now = datetime(2026, 1, 1, tzinfo=UTC)
    consume(session, auth_user.id, "analysis", settings, now=now)
    session.commit()
    with pytest.raises(AppError) as caught:
        consume(session, auth_user.id, "analysis", settings, now=now + timedelta(seconds=1))
    assert caught.value.status_code == 429
    consume(session, auth_user.id, "upload", settings, now=now)
    other = User(username="other-" + secrets.token_hex(8), password_hash="unused-test")
    session.add(other)
    session.flush()
    consume(session, other.id, "analysis", settings, now=now)
    consume(session, auth_user.id, "analysis", settings, now=now + timedelta(seconds=60))
    session.commit()
    assert (
        session.scalar(
            select(RateLimitBucket.count).where(
                RateLimitBucket.user_id == auth_user.id, RateLimitBucket.scope == "analysis"
            )
        )
        == 1
    )


def test_missing_auth_and_unknown_dataset_do_not_consume_analysis_budget(
    client, session, auth_user
):
    response = client.post(
        "/api/datasets/00000000-0000-0000-0000-000000000001/analyses", json={"question": "count"}
    )
    assert response.status_code == 404
    assert (
        session.scalar(
            select(func.count())
            .select_from(RateLimitBucket)
            .where(RateLimitBucket.user_id == auth_user.id)
        )
        == 0
    )
    client.cookies.clear()
    assert client.post("/api/datasets").status_code == 401
    assert (
        session.scalar(
            select(func.count())
            .select_from(RateLimitBucket)
            .where(RateLimitBucket.user_id == auth_user.id)
        )
        == 0
    )


def test_expired_quota_buckets_follow_retention_dry_run_and_apply(session, settings, auth_user):
    consume(
        session, auth_user.id, "analysis", settings, now=datetime.now(UTC) - timedelta(minutes=2)
    )
    session.commit()
    assert cleanup(session, settings)["rate_limits"] == 1
    assert session.get(RateLimitBucket, (auth_user.id, "analysis"))
    assert cleanup(session, settings, apply=True)["rate_limits"] == 1
    assert session.get(RateLimitBucket, (auth_user.id, "analysis")) is None


@pytest.mark.postgres
def test_real_postgres_concurrent_workers_cannot_exceed_shared_budget():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL for real concurrent PostgreSQL admission")
    engine = create_engine(url)
    config = Settings(_env_file=None, analysis_requests_per_window=3)
    now = datetime.now(UTC)
    with Session(engine) as session:
        user = User(username="quota-" + secrets.token_hex(8), password_hash="unused-test")
        session.add(user)
        session.commit()
        user_id = user.id
    try:

        def request(_):
            with Session(engine) as worker:
                try:
                    consume(worker, user_id, "analysis", config, now=now)
                    worker.commit()
                    return 201
                except AppError as error:
                    worker.rollback()
                    return error.status_code

        with ThreadPoolExecutor(max_workers=8) as workers:
            outcomes = list(workers.map(request, range(8)))
        assert outcomes.count(201) == 3 and outcomes.count(429) == 5
        with Session(engine) as worker:
            assert (
                worker.scalar(
                    select(RateLimitBucket.count).where(RateLimitBucket.user_id == user_id)
                )
                == 3
            )
    finally:
        with Session(engine) as session:
            session.execute(delete(RateLimitBucket).where(RateLimitBucket.user_id == user_id))
            session.execute(delete(User).where(User.id == user_id))
            session.commit()
        engine.dispose()
