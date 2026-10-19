import csv
import hashlib
import io
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.auth import UserDep
from app.datasets import SessionDep, SettingsDep, dataset_path, find_dataset
from app.errors import AppError
from app.execution import execute_query
from app.models import Analysis, AuditEvent, Dataset
from app.presentation import describe_result
from app.providers import LLMProvider, get_provider
from app.sql_guard import validate_sql

router = APIRouter(prefix="/api", tags=["analyses"])


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]


class AnalysisView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    dataset_id: str
    question: str
    sql: str | None
    explanation: str | None
    provider: str
    model: str
    status: str
    duration_ms: int
    returned_rows: int
    truncated: bool
    summary: dict
    error_code: str | None
    error_message: str | None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AnalysisDetail(AnalysisView):
    result: dict | None = None
    result_expired: bool = False


def result_path(settings, analysis_id: str) -> Path:
    return settings.data_dir / "results" / f"{uuid.UUID(analysis_id)}.json"


def find_analysis(session, analysis_id: uuid.UUID, owner_id: str):
    analysis = session.scalar(
        select(Analysis)
        .join(Dataset)
        .where(Analysis.id == str(analysis_id), Dataset.owner_id == owner_id)
    )
    if analysis is None:
        raise AppError(404, "analysis_not_found", "分析历史不存在。")
    return analysis


def detail(analysis: Analysis, settings) -> AnalysisDetail:
    view = AnalysisDetail.model_validate(analysis)
    path = result_path(settings, analysis.id)
    if analysis.status == "success":
        if path.is_file():
            view.result = json.loads(path.read_text(encoding="utf-8"))
        else:
            view.result_expired = True
    return view


@router.post("/datasets/{dataset_id}/analyses", response_model=AnalysisDetail, status_code=201)
def analyze(
    dataset_id: uuid.UUID,
    body: Question,
    session: SessionDep,
    settings: SettingsDep,
    user: UserDep,
    provider: Annotated[LLMProvider, Depends(get_provider)],
):
    # Serialize analysis creation against deletion of the same dataset.
    dataset = session.scalar(
        select(Dataset)
        .where(Dataset.id == str(dataset_id), Dataset.owner_id == user.id)
        .with_for_update()
    )
    if dataset is None:
        find_dataset(dataset_id, session, user.id)  # Same 404 for absent or foreign IDs.
    analysis = Analysis(
        id=str(uuid.uuid4()),
        dataset_id=str(dataset_id),
        question=body.question,
        provider=settings.llm_provider,
        model="deterministic-templates-v1"
        if settings.llm_provider == "mock"
        else settings.llm_model,
    )
    session.add(analysis)
    session.add(
        AuditEvent(event="analysis_started", dataset_id=str(dataset_id), analysis_id=analysis.id)
    )
    session.commit()
    started = time.perf_counter()
    path = result_path(settings, analysis.id)
    temporary = path.with_suffix(".tmp")
    try:
        proposal = provider.generate(body.question, dataset.profile["fields"])
        analysis.sql, analysis.explanation = proposal.sql, proposal.explanation
        sql = validate_sql(proposal.sql, dataset.profile["fields"])
        analysis.sql = sql
        result = execute_query(dataset_path(settings, str(dataset_id)), sql, settings)
        chart, summary = describe_result(result, settings.llm_provider, proposal.chart)
        result.update(chart=chart, summary=summary)
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > settings.max_result_bytes:
            raise AppError(413, "result_too_large", "序列化后的结果超过字节限制，请缩小查询。")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, path)
        analysis.status, analysis.summary = "success", summary
        analysis.returned_rows, analysis.truncated = len(result["rows"]), result["truncated"]
        analysis.duration_ms = int((time.perf_counter() - started) * 1000)
        session.add(
            AuditEvent(
                event="analysis_succeeded",
                dataset_id=str(dataset_id),
                analysis_id=analysis.id,
                details={"returned_rows": analysis.returned_rows, "truncated": analysis.truncated},
            )
        )
        session.commit()
        return detail(analysis, settings)
    except SQLAlchemyError:
        session.rollback()
        path.unlink(missing_ok=True)
        raise
    except Exception as error:
        path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        if isinstance(error, AppError):
            failure = error
        elif isinstance(error, OSError):
            failure = AppError(503, "result_storage_failed", "无法保存查询结果，未标记为成功。")
        else:
            failure = AppError(500, "analysis_failed", "分析过程异常，未产生成功结果。")
        analysis.status = "rejected" if failure.detail["code"] == "unsafe_sql" else "error"
        analysis.error_code, analysis.error_message = (
            failure.detail["code"],
            failure.detail["message"],
        )
        analysis.duration_ms = int((time.perf_counter() - started) * 1000)
        session.add(
            AuditEvent(
                event="analysis_rejected" if analysis.status == "rejected" else "analysis_failed",
                dataset_id=str(dataset_id),
                analysis_id=analysis.id,
                details={
                    "reason": analysis.error_code,
                    "sql_sha256": hashlib.sha256((analysis.sql or "").encode()).hexdigest(),
                },
            )
        )
        session.commit()
        raise AppError(
            failure.status_code,
            analysis.error_code,
            analysis.error_message,
            analysis_id=analysis.id,
        ) from error


@router.get("/analyses", response_model=list[AnalysisView])
def history(
    session: SessionDep,
    user: UserDep,
    dataset_id: uuid.UUID | None = None,
    q: Annotated[str, Query(max_length=200)] = "",
    status: Literal["running", "success", "rejected", "error"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    query = (
        select(Analysis)
        .join(Dataset)
        .where(Dataset.owner_id == user.id)
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
    )
    if dataset_id:
        query = query.where(Analysis.dataset_id == str(dataset_id))
    if term := q.strip():
        # Bound parameters and literal LIKE escaping; '%' and '_' are user text.
        query = query.where(
            or_(
                Analysis.question.icontains(term, autoescape=True),
                Analysis.sql.icontains(term, autoescape=True),
            )
        )
    if status:
        query = query.where(Analysis.status == status)
    return session.scalars(query.limit(limit).offset(offset)).all()


@router.get("/analyses/{analysis_id}", response_model=AnalysisDetail)
def get_analysis(analysis_id: uuid.UUID, session: SessionDep, settings: SettingsDep, user: UserDep):
    return detail(find_analysis(session, analysis_id, user.id), settings)


def csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value  # Spreadsheet formula injection defense.
    return value


@router.get("/analyses/{analysis_id}/export")
def export_csv(analysis_id: uuid.UUID, session: SessionDep, settings: SettingsDep, user: UserDep):
    analysis = find_analysis(session, analysis_id, user.id)
    view = detail(analysis, settings)
    if analysis.status != "success":
        raise AppError(409, "analysis_not_successful", "该分析没有成功结果可导出。")
    if view.result is None:
        raise AppError(410, "result_expired", "结果快照已清理，历史元信息仍可查看。")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    columns = [column["name"] for column in view.result["columns"]]
    writer.writerow([csv_cell(column) for column in columns])
    writer.writerows([csv_cell(row[column]) for column in columns] for row in view.result["rows"])
    return Response(
        content=stream.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="datapilot-{analysis.id}.csv"',
            "X-Result-Truncated": str(analysis.truncated).lower(),
        },
    )
