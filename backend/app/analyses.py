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

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.analysis_agent import PlannerContext, plan_analysis
from app.analysis_graph import AnalysisRun, execute_analysis_graph
from app.auth import UserDep
from app.datasets import SessionDep, SettingsDep, dataset_path, find_dataset
from app.errors import AppError
from app.evidence_snapshot import (
    audit_summary,
    begin_snapshot,
    capture_sources,
    validate_live_sources,
)
from app.execution import execute_query
from app.knowledge import metric_definition, retrieve_knowledge
from app.metric_resolution import MetricRegistry
from app.models import Analysis, AuditEvent, Dataset
from app.observability import emit, timed
from app.presentation import describe_result
from app.providers import LLMProvider, get_provider
from app.rate_limits import consume
from app.tracing import export_trace

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
    request_id: str | None
    telemetry: dict

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
    request: Request,
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
    consume(session, user.id, "analysis", settings)
    analysis = Analysis(
        id=str(uuid.uuid4()),
        dataset_id=str(dataset_id),
        question=body.question,
        request_id=request.state.request_id,
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
    timings = {}
    run = AnalysisRun(
        provider=provider,
        settings=settings,
        timings=timings,
        execute=lambda sql: execute_query(
            dataset_path(settings, str(dataset_id)),
            sql,
            settings.model_copy(
                update={
                    "query_timeout_seconds": min(
                        settings.query_timeout_seconds, max(0.01, run.deadline - time.monotonic())
                    )
                }
            ),
        ),
        question=body.question,
        fields=dataset.profile["fields"],
    )
    begin_snapshot(run.rag, settings.rag_evidence_policy)
    run.source_guard = lambda: validate_live_sources(
        session, user.id, str(dataset_id), run.rag, settings
    )

    def configure_evidence():
        registry = MetricRegistry(session, user.id, str(dataset_id), settings)
        strict_evidence = settings.rag_evidence_policy == "registered"
        resolution = registry.resolve(body.question, question=True)
        run.metric_resolution = registry.summary(resolution)
        requires_metric = (registry.enabled or strict_evidence) and (
            resolution["status"] != "not_found"
            or (
                (bool(registry.items) or strict_evidence)
                and not registry.explicit_native(body.question)
            )
        )
        if requires_metric and resolution["status"] == "resolved":
            run.registered_metrics = [i["contract"].model_dump() for i in resolution["items"]]
            run.evidence_guard = lambda sql: registry.check_sql(sql, resolution)

        metric_sources = [i["source"] for i in resolution["items"]] if run.evidence_guard else []
        catalog_candidates = [i["source"] for i in resolution["items"]]

        def observe(sources, *, origin_hint="unrecorded"):
            return capture_sources(
                session, user.id, str(dataset_id), run.rag, sources, origin_hint=origin_hint
            )

        def retrieve_snapshot():
            rag = retrieve_knowledge(
                session, user.id, str(dataset_id), body.question, settings, deadline=run.deadline
            )
            begin_snapshot(
                rag,
                settings.rag_evidence_policy,
                resolution["status"] if registry.enabled else "not_configured",
                [s["chunk_id"] for s in metric_sources],
            )
            initial = {
                c["chunk_id"]
                for stage in rag.get("candidate_stages", [])
                for c in stage.get("candidates", [])
            }
            origins = {s["chunk_id"]: s for s in rag["evidence"]["source_origins"]}
            for source in catalog_candidates:
                key = source["chunk_id"]
                origins.setdefault(
                    key,
                    {
                        "chunk_id": key,
                        "origin": "vector_candidate" if key in initial else "registered_catalog",
                    },
                )
            rag["evidence"]["source_origins"] = list(origins.values())
            run.rag = rag
            raw_ids = {s["chunk_id"] for s in rag.get("citations", [])}
            captured = observe(rag.get("citations", []) + catalog_candidates)
            rag["citations"] = [s for s in captured if s["chunk_id"] in raw_ids]
            return rag

        run.retriever = retrieve_snapshot

        def evidence_clarification():
            if strict_evidence:
                run.rag["citations"] = []
                run.rag.setdefault("evidence", {}).update(
                    selected_source_ids=[], used_source_ids=[], final_decision="clarify"
                )
            return {
                "needs_clarification": True,
                "clarification_questions": [registry.clarification(resolution)],
                "knowledge_chunk_ids": [],
            }

        if settings.agent_enabled:
            planner = PlannerContext(session, user.id, str(dataset_id), settings, run.deadline)
            planner.search = lambda query: retrieve_knowledge(
                session, user.id, str(dataset_id), query, settings, deadline=run.deadline
            )
            planner.metric_lookup = lambda name: metric_definition(
                session, user.id, str(dataset_id), name, settings
            )
            if strict_evidence:
                from app.retrieval_evidence import filter_evidence

                # Tool arguments cannot change the original request's evidence scope.
                def scoped_result(result):
                    checked = filter_evidence(
                        session, user.id, str(dataset_id), body.question, settings, result
                    )
                    returned = {s["chunk_id"] for s in result.get("citations", [])}
                    if any(s["chunk_id"] not in returned for s in checked["citations"]):
                        # Catalog supplementation belongs to initial retrieval.
                        # A tool asking for another metric must report no match,
                        # rather than silently returning the original metric.
                        checked.update(
                            status="no_match", citations=[], resolution_status="not_found"
                        )
                        checked["evidence"].update(
                            status="insufficient_evidence",
                            source_origins=[],
                            eligible_candidate_count=0,
                            catalog_supplement_count=0,
                        )
                    return checked

                planner.search = lambda query: scoped_result(
                    retrieve_knowledge(
                        session, user.id, str(dataset_id), query, settings, deadline=run.deadline
                    )
                )
                planner.metric_lookup = lambda name: scoped_result(
                    metric_definition(session, user.id, str(dataset_id), name, settings)
                )

            def make_plan():
                try:
                    if requires_metric and resolution["status"] != "resolved":
                        return evidence_clarification()
                    planner.knowledge = run.rag.get("citations", []).copy()
                    if requires_metric:
                        required = [i["source"] for i in resolution["items"]]
                        required_ids = {i["chunk_id"] for i in required}
                        planner.knowledge = (
                            required
                            + [i for i in planner.knowledge if i["chunk_id"] not in required_ids][
                                : 4 - len(required)
                            ]
                        )
                    planner.knowledge = observe(planner.knowledge)
                    plan = plan_analysis(planner, body.question)
                    planner.knowledge = observe(planner.knowledge, origin_hint="agent_tool")
                    if not set(plan.knowledge_chunk_ids) <= {
                        s["chunk_id"] for s in planner.knowledge
                    }:
                        raise AppError(
                            403, "evidence_source_denied", "规划来源未通过当次所有权校验。"
                        )
                    if (
                        registry.enabled
                        and not requires_metric
                        and not registry.explicit_native(body.question)
                    ):
                        if plan.metrics and not plan.needs_clarification:
                            plan.needs_clarification = True
                            plan.clarification_questions = [
                                "没有可验证的已登记指标，请明确计算字段与业务口径。"
                            ]
                    if requires_metric and not plan.needs_clarification:
                        expected = {i["source"]["chunk_id"] for i in resolution["items"]}
                        if not expected <= set(plan.knowledge_chunk_ids):
                            plan.needs_clarification = True
                            plan.clarification_questions = [
                                "规划未采用已登记指标来源，请确认指标定义。"
                            ]
                    run.rag["retrieved_count"] = len(planner.knowledge)
                    run.rag["citations"] = [
                        item
                        for item in planner.knowledge
                        if item["chunk_id"] in plan.knowledge_chunk_ids
                    ]
                    if strict_evidence:
                        run.rag["evidence"]["selected_source_ids"] = [
                            i["chunk_id"] for i in run.rag["citations"]
                        ]
                    return plan.model_dump()
                finally:
                    observe(planner.knowledge, origin_hint="agent_tool")
                    run.agent_metrics = planner.metadata()
                    run.agent_spans = planner.spans.copy()

            run.planner = make_plan
        elif requires_metric:

            def plan_without_agent():
                if resolution["status"] != "resolved":
                    return evidence_clarification()
                run.rag["citations"] = [i["source"] for i in resolution["items"]]
                run.rag["retrieved_count"] = len(run.rag["citations"])
                return {
                    "needs_clarification": False,
                    "knowledge_chunk_ids": [i["source"]["chunk_id"] for i in resolution["items"]],
                }

            run.planner = plan_without_agent

    identifiers = {
        "request_id": analysis.request_id,
        "analysis_id": analysis.id,
        "user_id": user.id,
    }

    def telemetry():
        return {
            "timings_ms": timings.copy(),
            "usage": run.usage().model_dump(),
            "workflow": run.metadata(),
        }

    def log_completion(status, error_code, duration_ms):
        export_trace(settings, run, status, duration_ms)
        emit(
            "analysis_completed",
            **identifiers,
            status=status,
            error_code=error_code,
            duration_ms=duration_ms,
            **telemetry(),
        )

    try:
        configure_evidence()
        execute_analysis_graph(run)
        analysis.sql, analysis.explanation = run.sql, run.explanation
        if run.plan.get("needs_clarification"):
            analysis.status = "clarification"
            analysis.summary = {"clarification_questions": run.plan["clarification_questions"]}
            analysis.duration_ms = int((time.perf_counter() - started) * 1000)
            analysis.telemetry = telemetry()
            session.add(
                AuditEvent(
                    event="analysis_clarification",
                    dataset_id=str(dataset_id),
                    analysis_id=analysis.id,
                    details=audit_summary(run),
                )
            )
            session.commit()
            log_completion("clarification", None, analysis.duration_ms)
            return detail(analysis, settings)
        result = run.result
        with timed(timings, "persist"):
            chart, summary = describe_result(result, settings.llm_provider, run.chart)
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
        analysis.telemetry = telemetry()
        session.add(
            AuditEvent(
                event="analysis_succeeded",
                dataset_id=str(dataset_id),
                analysis_id=analysis.id,
                details={
                    "returned_rows": analysis.returned_rows,
                    "truncated": analysis.truncated,
                    **audit_summary(run),
                },
            )
        )
        session.commit()
        log_completion(analysis.status, analysis.error_code, analysis.duration_ms)
        return detail(analysis, settings)
    except SQLAlchemyError:
        session.rollback()
        path.unlink(missing_ok=True)
        run.failure = AppError(503, "storage_unavailable", "无法保存分析历史。")
        run.rag["evidence"]["final_decision"] = "error"
        log_completion("error", "storage_unavailable", int((time.perf_counter() - started) * 1000))
        raise
    except Exception as error:
        analysis.sql, analysis.explanation = run.sql, run.explanation
        path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        if isinstance(error, AppError):
            failure = error
        elif isinstance(error, OSError):
            failure = AppError(503, "result_storage_failed", "无法保存查询结果，未标记为成功。")
        else:
            failure = AppError(500, "analysis_failed", "分析过程异常，未产生成功结果。")
        run.failure = failure
        analysis.status = "rejected" if failure.detail["code"] == "unsafe_sql" else "error"
        analysis.error_code, analysis.error_message = (
            failure.detail["code"],
            failure.detail["message"],
        )
        if "evidence" in run.rag:
            run.rag["evidence"]["final_decision"] = (
                "reject"
                if failure.detail["code"] in {"unsafe_sql", "metric_contract_violation"}
                else "error"
            )
        analysis.duration_ms = int((time.perf_counter() - started) * 1000)
        analysis.telemetry = telemetry()
        session.add(
            AuditEvent(
                event="analysis_rejected" if analysis.status == "rejected" else "analysis_failed",
                dataset_id=str(dataset_id),
                analysis_id=analysis.id,
                details={
                    "reason": analysis.error_code,
                    "sql_sha256": hashlib.sha256((analysis.sql or "").encode()).hexdigest(),
                    **audit_summary(run),
                },
            )
        )
        session.commit()
        log_completion(analysis.status, analysis.error_code, analysis.duration_ms)
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
    status: Literal["running", "success", "rejected", "error", "clarification"] | None = None,
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
