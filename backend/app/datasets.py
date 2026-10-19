import os
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import UserDep
from app.config import Settings, get_settings
from app.db import get_session
from app.errors import AppError
from app.ingestion import import_dataset
from app.models import Analysis, AuditEvent, Dataset
from app.rate_limits import consume

router = APIRouter(prefix="/api/datasets", tags=["datasets"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class DatasetSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    file_type: str
    sheet_name: str | None
    row_count: int
    column_count: int


class DatasetView(DatasetSummary):
    profile: dict


def dataset_path(settings: Settings, dataset_id: str) -> Path:
    return settings.data_dir / "datasets" / f"{uuid.UUID(dataset_id)}.duckdb"


def find_dataset(dataset_id: uuid.UUID, session: Session, owner_id: str) -> Dataset:
    dataset = session.scalar(
        select(Dataset).where(Dataset.id == str(dataset_id), Dataset.owner_id == owner_id)
    )
    if dataset is None:
        raise AppError(404, "dataset_not_found", "数据集不存在。")
    return dataset


@router.get("", response_model=list[DatasetSummary])
def list_datasets(session: SessionDep, user: UserDep):
    return session.scalars(
        select(Dataset)
        .where(Dataset.owner_id == user.id)
        .order_by(Dataset.created_at.desc())
        .limit(100)
    ).all()


@router.get("/{dataset_id}", response_model=DatasetView)
def get_dataset(dataset_id: uuid.UUID, session: SessionDep, user: UserDep):
    return find_dataset(dataset_id, session, user.id)


@router.post("", response_model=DatasetView, status_code=201)
def upload_dataset(
    session: SessionDep,
    settings: SettingsDep,
    user: UserDep,
    file: Annotated[UploadFile, File()],
    sheet: Annotated[str | None, Form()] = None,
):
    name = (file.filename or "upload").replace("\\", "/").split("/")[-1][:255]
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise AppError(415, "unsupported_format", "仅支持 .csv 和 .xlsx。")
    consume(session, user.id, "upload", settings)
    session.commit()  # Parsing failures still consume upload capacity.
    # Never trust Content-Type or Content-Length supplied by a client.
    content = file.file.read(settings.max_upload_bytes + 1)
    dataset_id = str(uuid.uuid4())
    target = dataset_path(settings, dataset_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"upload-{dataset_id}.duckdb")
    try:
        profile, selected = import_dataset(content, suffix, sheet, temporary, settings)
        dataset = Dataset(
            id=dataset_id,
            owner_id=user.id,
            name=name,
            file_type=suffix[1:],
            sheet_name=selected,
            row_count=profile["row_count"],
            column_count=profile["column_count"],
            profile=profile,
        )
        os.replace(temporary, target)
        session.add(dataset)
        session.add(
            AuditEvent(
                event="dataset_uploaded",
                dataset_id=dataset_id,
                details={"rows": dataset.row_count, "columns": dataset.column_count},
            )
        )
        session.commit()
        session.refresh(dataset)
        return dataset
    except Exception:
        session.rollback()
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


def remove_dataset(session: Session, settings: Settings, dataset: Dataset):
    running = session.scalar(
        select(func.count())
        .select_from(Analysis)
        .where(
            Analysis.dataset_id == dataset.id,
            Analysis.status == "running",
        )
    )
    if running:
        raise AppError(409, "dataset_busy", "数据集仍有分析在执行，请稍后删除。")
    analysis_ids = session.scalars(
        select(Analysis.id).where(Analysis.dataset_id == dataset.id)
    ).all()
    target = dataset_path(settings, dataset.id)
    trash = target.with_suffix(".deleting")
    moved = False
    try:
        if target.exists():
            target.replace(trash)
            moved = True
        session.execute(delete(Analysis).where(Analysis.dataset_id == dataset.id))
        session.add(AuditEvent(event="dataset_deleted", dataset_id=dataset.id))
        session.delete(dataset)
        session.commit()
    except Exception:
        session.rollback()
        if moved:
            trash.replace(target)
        raise
    # Unreferenced files left after DB commit can be retried by maintenance.
    for path in [trash, *[settings.data_dir / "results" / f"{item}.json" for item in analysis_ids]]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: uuid.UUID, session: SessionDep, settings: SettingsDep, user: UserDep
):
    dataset = session.scalar(
        select(Dataset)
        .where(Dataset.id == str(dataset_id), Dataset.owner_id == user.id)
        .with_for_update()
    )
    if dataset is None:
        raise AppError(404, "dataset_not_found", "数据集不存在。")
    remove_dataset(session, settings, dataset)
    return Response(status_code=204)
