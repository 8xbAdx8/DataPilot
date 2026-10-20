"""Operator-only account provisioning and explicit ownership of legacy uploads."""

import argparse
import getpass
import sys
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import hash_password, lock_attempt, normalize_username
from app.db import get_engine
from app.models import AuditEvent, AuthSession, Dataset, User


def create_user(session: Session, username: str, password: str) -> User:
    username = normalize_username(username)
    if session.scalar(select(User.id).where(User.username == username)):
        raise ValueError("该用户名已存在，未修改密码或数据。")
    user = User(username=username, password_hash=hash_password(password))
    session.add(user)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ValueError("该用户名已存在，未修改密码或数据。") from error
    return user


def reset_password(session: Session, username: str, password: str) -> User:
    """Operator recovery: password, sessions, guessing window and audit commit together."""
    username = normalize_username(username)
    if session.scalar(select(User.id).where(User.username == username)) is None:
        raise ValueError("用户不存在，未创建或修改账户。")
    encoded = hash_password(password)
    try:
        attempt = lock_attempt(session, username)
        user = session.scalar(
            select(User)
            .where(User.username == username)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise ValueError("用户不存在。")
        user.password_hash = encoded
        count = session.execute(delete(AuthSession).where(AuthSession.user_id == user.id)).rowcount
        attempt.failures, attempt.window_started = 0, datetime.now(UTC)
        session.add(
            AuditEvent(
                event="password_reset",
                details={"user_id": user.id, "revoked_sessions": count, "source": "operator_cli"},
            )
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    return user


def assign_legacy(session: Session, username: str, *, apply=False) -> int:
    user = session.scalar(select(User).where(User.username == normalize_username(username)))
    if user is None:
        raise ValueError("用户不存在。")
    count = session.scalar(
        select(func.count()).select_from(Dataset).where(Dataset.owner_id.is_(None))
    )
    if apply:
        changed = session.execute(
            update(Dataset).where(Dataset.owner_id.is_(None)).values(owner_id=user.id)
        )
        count = changed.rowcount
        session.add(
            AuditEvent(
                event="legacy_datasets_assigned", details={"user_id": user.id, "count": count}
            )
        )
        session.commit()
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create-user", "reset-password", "assign-legacy"])
    parser.add_argument("--username", required=True)
    parser.add_argument(
        "--password-stdin", action="store_true", help="Read one password line from stdin"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Apply legacy ownership; default is dry-run"
    )
    args = parser.parse_args()
    try:
        with Session(get_engine()) as session:
            if args.action in {"create-user", "reset-password"}:
                if args.password_stdin:
                    password = sys.stdin.readline().rstrip("\r\n")
                else:
                    password = getpass.getpass("Password (12–128 characters): ")
                    if password != getpass.getpass("Confirm password: "):
                        raise ValueError("两次密码不一致。")
                action = create_user if args.action == "create-user" else reset_password
                user = action(session, args.username, password)
                label = "Created user" if args.action == "create-user" else "Reset password"
                print(f"{label}: {user.username}")
            else:
                count = assign_legacy(session, args.username, apply=args.apply)
                mode = "Assigned" if args.apply else "Dry-run: would assign"
                print(f"{mode} {count} unowned datasets")
    except ValueError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
