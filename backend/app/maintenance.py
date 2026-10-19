"""Explicit retention runner. Default is dry-run; --apply mutates only DataPilot-owned paths."""

import argparse
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.analyses import result_path
from app.config import Settings, get_settings
from app.datasets import remove_dataset
from app.db import get_engine
from app.models import Analysis, AuditEvent, AuthSession, Dataset, LoginAttempt


def cleanup(session: Session, settings: Settings, *, apply: bool = False, now=None):
    now = now or datetime.now(UTC)
    counts = {
        "stale_requests": 0,
        "snapshots": 0,
        "histories": 0,
        "datasets": 0,
        "audits": 0,
        "orphans": 0,
        "sessions": 0,
        "login_attempts": 0,
    }
    for model, field, cutoff, key in [
        (AuthSession, AuthSession.expires_at, now, "sessions"),
        (LoginAttempt, LoginAttempt.window_started, now - timedelta(minutes=15), "login_attempts"),
    ]:
        expired = session.scalars(select(model).where(field <= cutoff)).all()
        counts[key] = len(expired)
        if apply:
            # Recheck the expiry predicate after any concurrent login row lock.
            # Deleting only by primary key could erase a newly reset failure window.
            counts[key] = session.execute(delete(model).where(field <= cutoff)).rowcount
    if apply:
        session.commit()
    stale = session.scalars(
        select(Analysis).where(
            Analysis.status == "running", Analysis.created_at < now - timedelta(minutes=10)
        )
    ).all()
    counts["stale_requests"] = len(stale)
    stale_ids = {item.id for item in stale}
    if apply:
        for item in stale:
            item.status, item.error_code = "error", "request_interrupted"
            item.error_message = "请求被中断，未生成成功结果。"
            session.add(
                AuditEvent(
                    event="analysis_interrupted", analysis_id=item.id, dataset_id=item.dataset_id
                )
            )
        session.commit()
    for item in session.scalars(
        select(Analysis).where(Analysis.created_at < now - timedelta(days=7))
    ).all():
        path = result_path(settings, item.id)
        eligible = item.status != "running" or item.id in stale_ids
        if path.exists() and eligible:
            counts["snapshots"] += 1
            if apply:
                path.unlink(missing_ok=True)
        created = (
            item.created_at.replace(tzinfo=UTC)
            if item.created_at.tzinfo is None
            else item.created_at.astimezone(UTC)
        )
        if created < now - timedelta(days=30) and eligible:
            counts["histories"] += 1
            if apply:
                session.delete(item)
    if apply:
        session.commit()
    for dataset in session.scalars(
        select(Dataset).where(Dataset.created_at < now - timedelta(days=30))
    ).all():
        running = session.scalars(
            select(Analysis.id).where(
                Analysis.dataset_id == dataset.id, Analysis.status == "running"
            )
        ).all()
        if any(item_id not in stale_ids for item_id in running):
            continue
        counts["datasets"] += 1
        if apply:
            remove_dataset(session, settings, dataset)
    for event in session.scalars(
        select(AuditEvent).where(AuditEvent.created_at < now - timedelta(days=90))
    ).all():
        counts["audits"] += 1
        if apply:
            session.delete(event)
    if apply:
        session.commit()
    dataset_ids = set(session.scalars(select(Dataset.id)).all())
    analysis_ids = set(session.scalars(select(Analysis.id)).all())
    # Only UUID files at least 24h old. Never recurse or delete directories.
    for directory, suffixes, known in [
        ("results", {".json", ".tmp"}, analysis_ids),
        ("datasets", {".duckdb", ".deleting"}, dataset_ids),
    ]:
        folder = settings.data_dir / directory
        if not folder.exists():
            continue
        for path in folder.iterdir():
            if not path.is_file() or path.suffix not in suffixes:
                continue
            try:
                item_id = str(uuid.UUID(path.stem.removeprefix("upload-")))
            except ValueError:
                continue
            if (
                item_id not in known
                and path.stat().st_mtime < (now - timedelta(days=1)).timestamp()
            ):
                counts["orphans"] += 1
                if apply:
                    path.unlink(missing_ok=True)
            elif directory == "datasets" and path.suffix == ".deleting" and item_id in known:
                original = path.with_suffix(".duckdb")
                if apply and not original.exists():
                    path.replace(original)  # Recover crash between rename and DB commit.
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Apply retention; omitted means dry-run"
    )
    args = parser.parse_args()
    with Session(get_engine()) as session:
        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry-run",
                    **cleanup(session, get_settings(), apply=args.apply),
                }
            )
        )


if __name__ == "__main__":
    main()
