from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.analyses import result_path
from app.datasets import dataset_path
from app.maintenance import cleanup
from app.models import Analysis, AuditEvent, Dataset


def imported(client):
    return client.post("/api/datasets", files={"file": ("retention.csv", b"a,b\n1,2\n")}).json()


def test_delete_removes_metadata_rows_snapshots_and_duckdb(client, settings, session):
    dataset = imported(client)
    analysis = client.post(
        f"/api/datasets/{dataset['id']}/analyses", json={"question": "行数"}
    ).json()
    assert client.delete(f"/api/datasets/{dataset['id']}").status_code == 204
    assert client.get(f"/api/datasets/{dataset['id']}").status_code == 404
    assert client.get(f"/api/analyses/{analysis['id']}").status_code == 404
    assert not dataset_path(settings, dataset["id"]).exists()
    assert not result_path(settings, analysis["id"]).exists()
    events = session.scalars(select(AuditEvent).where(AuditEvent.dataset_id == dataset["id"])).all()
    assert "dataset_deleted" in {event.event for event in events}


def test_delete_restores_file_on_failed_database_commit(client, settings, session, monkeypatch):
    dataset = imported(client)

    def fail():
        raise SQLAlchemyError("private detail")

    monkeypatch.setattr(session, "commit", fail)
    assert client.delete(f"/api/datasets/{dataset['id']}").status_code == 503
    assert dataset_path(settings, dataset["id"]).is_file()


def test_retention_dry_run_then_apply_preserves_history_until_30_days(client, settings, session):
    dataset = imported(client)
    analysis = client.post(
        f"/api/datasets/{dataset['id']}/analyses", json={"question": "行数"}
    ).json()
    now = datetime.now(UTC)
    record = session.get(Analysis, analysis["id"])
    record.created_at = now - timedelta(days=8)
    event = AuditEvent(event="old_audit", created_at=now - timedelta(days=91))
    session.add(event)
    session.commit()
    counts = cleanup(session, settings, now=now)
    assert counts["snapshots"] == 1 and counts["audits"] == 1
    assert result_path(settings, record.id).exists()
    cleanup(session, settings, apply=True, now=now)
    assert not result_path(settings, record.id).exists()
    assert session.get(Analysis, record.id) is not None
    assert session.get(AuditEvent, event.id) is None
    record.created_at = now - timedelta(days=31)
    session.get(Dataset, dataset["id"]).created_at = now - timedelta(days=31)
    session.commit()
    counts = cleanup(session, settings, apply=True, now=now)
    assert counts["histories"] == 1 and counts["datasets"] == 1
    assert not dataset_path(settings, dataset["id"]).exists()


def test_running_dataset_cannot_be_deleted_and_interrupted_request_is_recovered(
    client, settings, session
):
    dataset = imported(client)
    record = Analysis(
        dataset_id=dataset["id"],
        question="interrupted",
        provider="mock",
        model="test",
        created_at=datetime.now(UTC) - timedelta(minutes=11),
        status="running",
    )
    session.add(record)
    session.commit()
    assert client.delete(f"/api/datasets/{dataset['id']}").status_code == 409
    assert cleanup(session, settings, apply=True)["stale_requests"] == 1
    assert session.get(Analysis, record.id).status == "error"
    assert client.delete(f"/api/datasets/{dataset['id']}").status_code == 204


def test_dry_run_predicts_expired_dataset_after_stale_request_recovery(client, settings, session):
    dataset = imported(client)
    now = datetime.now(UTC)
    session.get(Dataset, dataset["id"]).created_at = now - timedelta(days=31)
    session.add(
        Analysis(
            dataset_id=dataset["id"],
            question="interrupted",
            provider="mock",
            model="test",
            status="running",
            created_at=now - timedelta(minutes=11),
        )
    )
    session.commit()
    predicted = cleanup(session, settings, now=now)
    assert predicted["stale_requests"] == 1 and predicted["datasets"] == 1
    assert dataset_path(settings, dataset["id"]).exists()
    applied = cleanup(session, settings, apply=True, now=now)
    assert applied["datasets"] == predicted["datasets"]
