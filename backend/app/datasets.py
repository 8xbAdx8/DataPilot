import os
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.errors import AppError
from app.ingestion import import_dataset
from app.models import Dataset

router = APIRouter(prefix="/api/datasets", tags=["datasets"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class DatasetView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    file_type: str
    sheet_name: str | None
    row_count: int
    column_count: int
    profile: dict


def dataset_path(settings: Settings, dataset_id: str) -> Path:
    return settings.data_dir / "datasets" / f"{uuid.UUID(dataset_id)}.duckdb"


def find_dataset(dataset_id: uuid.UUID, session: Session) -> Dataset:
    dataset = session.get(Dataset, str(dataset_id))
    if dataset is None:
        raise AppError(404, "dataset_not_found", "数据集不存在。")
    return dataset


@router.get("", response_model=list[DatasetView])
def list_datasets(session: SessionDep):
    return session.scalars(select(Dataset).order_by(Dataset.created_at.desc()).limit(100)).all()


@router.get("/{dataset_id}", response_model=DatasetView)
def get_dataset(dataset_id: uuid.UUID, session: SessionDep):
    return find_dataset(dataset_id, session)


@router.post("", response_model=DatasetView, status_code=201)
def upload_dataset(
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    sheet: Annotated[str | None, Form()] = None,
):
    name = (file.filename or "upload").replace("\\", "/").split("/")[-1][:255]
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise AppError(415, "unsupported_format", "仅支持 .csv 和 .xlsx。")
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
            name=name,
            file_type=suffix[1:],
            sheet_name=selected,
            row_count=profile["row_count"],
            column_count=profile["column_count"],
            profile=profile,
        )
        os.replace(temporary, target)
        session.add(dataset)
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
