"""Per-request StateGraph. Dependencies and result rows never enter graph state."""

import hashlib
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from app.config import Settings
from app.errors import AppError
from app.observability import timed
from app.providers import LLMProvider, SqlProposal, TokenUsage
from app.sql_guard import validate_sql


class AnalysisState(TypedDict, total=False):
    question: str
    schema: list[dict]
    proposal: dict
    sql: str
    repair_attempts: int
    repairable: bool
    status: str
    plan: dict
    rag: dict


@dataclass
class AnalysisRun:
    provider: LLMProvider
    settings: Settings
    timings: dict
    execute: Callable[[str], dict]
    question: str
    fields: list[dict]
    sql: str | None = None
    explanation: str | None = None
    chart: str = "auto"
    result: dict | None = None
    failure: AppError | None = None
    deadline: float = field(init=False)
    nodes: list[str] = field(default_factory=list)
    node_timings: dict = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    node_spans: list[dict] = field(default_factory=list)
    agent_spans: list[dict] = field(default_factory=list)
    validations: list[dict] = field(default_factory=list)
    repairs: int = 0
    sql_calls: int = 0
    sql_usages: list[TokenUsage] = field(default_factory=list)
    planner: Callable[[], dict] | None = None
    evidence_guard: Callable[[str], None] | None = None
    registered_metrics: list[dict] = field(default_factory=list)
    metric_resolution: dict = field(default_factory=dict)
    plan: dict = field(default_factory=dict)
    agent_metrics: dict = field(default_factory=dict)
    retriever: Callable[[], dict] | None = None
    rag: dict = field(
        default_factory=lambda: {"status": "not_requested", "used": False, "citations": []}
    )

    def generation_context(self):
        return {
            "analysis_plan": self.plan,
            "business_knowledge": self.rag.get("citations", []),
            "registered_metrics": self.registered_metrics,
        }

    def __post_init__(self):
        self.deadline = time.monotonic() + self.settings.workflow_timeout_seconds
        self.fields = [{"name": item["name"], "type": item["type"]} for item in self.fields]

    def metadata(self):
        return {
            "engine": "langgraph",
            "nodes": self.nodes.copy(),
            "stage_timings_ms": self.node_timings.copy(),
            "repair_attempts": self.repairs,
            "sql_model_calls": self.sql_calls,
            "validations": self.validations.copy(),
            "plan": self.plan.copy(),
            "agent": self.agent_metrics.copy(),
            "rag": self.rag.copy(),
            "metric_resolution": self.metric_resolution.copy(),
        }

    def budget(self):
        if time.monotonic() >= self.deadline:
            raise AppError(408, "workflow_timeout", "分析工作流超过总时间限制。")

    @contextmanager
    def measure(self, name):
        previous = self.timings.get(name, 0)
        try:
            with timed(self.timings, name):
                yield
        finally:
            self.timings[name] = self.timings.get(name, 0) + previous

    def usage(self):
        if not self.sql_usages:
            value = getattr(self.provider, "usage", None)
            return value if isinstance(value, TokenUsage) else TokenUsage()
        sources = {usage.source for usage in self.sql_usages}
        counts = {}
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            values = [getattr(usage, name) for usage in self.sql_usages]
            counts[name] = sum(values) if all(value is not None for value in values) else None
        return TokenUsage(source=sources.pop() if len(sources) == 1 else "unavailable", **counts)


def build_graph(run: AnalysisRun):
    def node(name, function):
        def wrapped(state):
            run.budget()
            run.nodes.append(name)
            started = time.perf_counter()
            span = {"name": name, "start": datetime.now(UTC), "status": "error"}
            try:
                update = function(state)
                run.budget()
                span["status"] = "rejected" if update.get("status") == "rejected" else "success"
                return update
            finally:
                span["end"] = datetime.now(UTC)
                run.node_spans.append(span)
                run.node_timings[name] = run.node_timings.get(name, 0) + int(
                    (time.perf_counter() - started) * 1000
                )

        return wrapped

    def generate(state):
        run.sql_calls += 1
        if hasattr(run.provider, "deadline"):
            run.provider.deadline = run.deadline
        try:
            with run.measure("provider"):
                contextual = getattr(run.provider, "generate_with_context", None)
                run.rag["used"] = bool(run.rag.get("citations") and callable(contextual))
                if "evidence" in run.rag:
                    run.rag["evidence"]["context_source_ids"] = (
                        [i["chunk_id"] for i in run.rag.get("citations", [])]
                        if run.rag["used"]
                        else []
                    )
                proposal = (
                    contextual(state["question"], state["schema"], run.generation_context())
                    if (run.plan or run.rag.get("citations")) and callable(contextual)
                    else run.provider.generate(state["question"], state["schema"])
                )
        finally:
            usage = getattr(run.provider, "usage", None)
            run.sql_usages.append(usage if isinstance(usage, TokenUsage) else TokenUsage())
        proposal = SqlProposal.model_validate(proposal)
        run.sql, run.explanation, run.chart = proposal.sql, proposal.explanation, proposal.chart
        return {"proposal": proposal.model_dump()}

    def guard(state):
        try:
            with run.measure("sql_guard"):
                sql = validate_sql(state["proposal"]["sql"], state["schema"])
                if run.evidence_guard:
                    run.evidence_guard(sql)
            run.validations.append({"status": "approved", "attempt": run.repairs})
            run.sql = sql
            run.failure = None
            return {"sql": sql, "status": "approved", "repairable": False}
        except AppError as error:
            run.failure = error
            if "evidence" in run.rag:
                run.rag["evidence"]["final_decision"] = "reject"
            run.validations.append(
                {
                    "status": "rejected",
                    "attempt": run.repairs,
                    "code": error.detail["code"],
                    "sql_sha256": hashlib.sha256(state["proposal"]["sql"].encode()).hexdigest(),
                }
            )
            return {"status": "rejected", "repairable": getattr(error, "repairable", False)}

    def route(state):
        if state["status"] == "approved":
            return "execute"
        if (
            state["repairable"]
            and run.settings.sql_repair_enabled
            and run.repairs == 0
            and callable(getattr(run.provider, "repair", None))
            and (
                not run.rag.get("used")
                or callable(getattr(run.provider, "repair_with_context", None))
            )
        ):
            return "repair"
        return END

    def repair(state):
        run.repairs += 1
        run.sql_calls += 1
        try:
            with run.measure("provider"):
                contextual = getattr(run.provider, "repair_with_context", None)
                proposal = (
                    contextual(
                        state["question"],
                        state["schema"],
                        state["proposal"]["sql"],
                        run.generation_context(),
                    )
                    if callable(contextual)
                    else run.provider.repair(
                        state["question"], state["schema"], state["proposal"]["sql"]
                    )
                )
        finally:
            usage = getattr(run.provider, "usage", None)
            run.sql_usages.append(usage if isinstance(usage, TokenUsage) else TokenUsage())
        proposal = SqlProposal.model_validate(proposal)
        run.sql, run.explanation, run.chart = proposal.sql, proposal.explanation, proposal.chart
        return {"proposal": proposal.model_dump(), "repair_attempts": run.repairs}

    def execute(state):
        with run.measure("query"):
            run.result = run.execute(state["sql"])
        if "evidence" in run.rag:
            run.rag["evidence"]["used_source_ids"] = run.rag["evidence"].get(
                "context_source_ids", []
            )
        return {"status": "executed"}

    def check(_):
        if not isinstance(run.result, dict) or not all(
            key in run.result for key in ("columns", "rows", "truncated")
        ):
            raise AppError(422, "query_failed", "查询结果结构不符合约定。")
        if "evidence" in run.rag:
            run.rag["evidence"]["final_decision"] = "answer"
        return {"status": "success"}

    graph = StateGraph(AnalysisState)
    graph.add_node("prepare", node("prepare", lambda _: {"repair_attempts": 0}))

    def retrieve(_):
        run.rag = run.retriever()
        return {"rag": run.rag}

    if run.retriever:
        graph.add_node("retrieve", node("retrieve", retrieve))

    def plan(_):
        run.plan = run.planner()
        if "evidence" in run.rag:
            run.rag["evidence"]["selected_source_ids"] = run.plan.get("knowledge_chunk_ids", [])
            if run.plan.get("needs_clarification"):
                run.rag["evidence"]["final_decision"] = "clarify"
        return {
            "plan": run.plan,
            "status": "clarification" if run.plan.get("needs_clarification") else "planned",
        }

    if run.planner:
        graph.add_node("plan", node("plan", plan))
    for name, function in (
        ("generate", generate),
        ("validate", guard),
        ("repair", repair),
        ("execute", execute),
        ("check_result", check),
    ):
        graph.add_node(name, node(name, function))
    graph.add_edge(START, "prepare")
    first = "retrieve" if run.retriever else "prepare"
    if run.retriever:
        graph.add_edge("prepare", "retrieve")
    if run.planner:
        graph.add_edge(first, "plan")
        graph.add_conditional_edges(
            "plan",
            lambda state: END if state["status"] == "clarification" else "generate",
            [END, "generate"],
        )
    else:
        graph.add_edge(first, "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges("validate", route, ["execute", "repair", END])
    graph.add_edge("repair", "validate")
    graph.add_edge("execute", "check_result")
    graph.add_edge("check_result", END)
    return graph.compile()


def execute_analysis_graph(run: AnalysisRun):
    # Suppress implicit LangChain/LangGraph input/output export, even if a shell
    # has LANGSMITH_TRACING=true. Explicit metadata-only export is separate.
    with tracing_context(enabled=False):
        state = build_graph(run).invoke(
            {"question": run.question, "schema": run.fields}, {"recursion_limit": 12}
        )
    if run.failure is not None:
        raise run.failure
    return state
