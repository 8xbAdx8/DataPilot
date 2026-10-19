"""Operator-only account provisioning and explicit ownership of legacy uploads."""

import argparse
import getpass
import sys

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import hash_password, normalize_username
from app.db import get_engine
from app.models import AuditEvent, Dataset, User


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
    parser.add_argument("action", choices=["create-user", "assign-legacy"])
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
            if args.action == "create-user":
                if args.password_stdin:
                    password = sys.stdin.readline().rstrip("\r\n")
                else:
                    password = getpass.getpass("Password (12–128 characters): ")
                    if password != getpass.getpass("Confirm password: "):
                        raise ValueError("两次密码不一致。")
                user = create_user(session, args.username, password)
                print(f"Created user: {user.username}")
            else:
                count = assign_legacy(session, args.username, apply=args.apply)
                mode = "Assigned" if args.apply else "Dry-run: would assign"
                print(f"{mode} {count} unowned datasets")
    except ValueError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
