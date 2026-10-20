import json

import pytest

from app.analysis_graph import AnalysisRun, execute_analysis_graph
from app.config import Settings
from app.errors import AppError
from app.providers import SqlProposal, TokenUsage

FIELDS = [{"name": "value", "type": "INTEGER"}]
RESULT = {"columns": [{"name": "n", "type": "BIGINT"}], "rows": [{"n": 3}], "truncated": False}


class RepairProvider:
    usage = TokenUsage(source="mock", prompt_tokens=0, completion_tokens=0, total_tokens=0)

    def __init__(self, first="SELECT missing FROM data", second="SELECT COUNT(*) AS n FROM data"):
        self.first, self.second, self.repairs = first, second, 0

    def generate(self, *_):
        return SqlProposal(sql=self.first, explanation="Synthetic")

    def repair(self, *_):
        self.repairs += 1
        return SqlProposal(sql=self.second, explanation="Repaired synthetic")


def make_run(provider, execute=None, **config):
    return AnalysisRun(
        provider,
        Settings(_env_file=None, **config),
        {},
        execute or (lambda _: RESULT.copy()),
        "行数",
        FIELDS,
    )


def test_schema_repair_runs_once_and_reenters_full_guard():
    executed = []
    run = make_run(RepairProvider(), lambda sql: executed.append(sql) or RESULT.copy())
    state = execute_analysis_graph(run)
    assert state["status"] == "success" and len(executed) == 1
    assert run.nodes == [
        "prepare",
        "generate",
        "validate",
        "repair",
        "validate",
        "execute",
        "check_result",
    ]
    assert run.repairs == 1 and run.sql_calls == 2
    assert [v["status"] for v in run.validations] == ["rejected", "approved"]
    assert run.result["rows"] == [{"n": 3}]
    assert "rows" not in json.dumps(state) and "settings" not in state and "path" not in state
    assert run.sql not in json.dumps(run.metadata())
    assert not {"question", "proposal", "rows", "schema"}.intersection(run.metadata())


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE data",
        "SELECT * FROM data; DELETE FROM data",
        "SELECT * FROM users",
        "SELECT * FROM read_csv('/secret')",
        "WITH a AS (SELECT * FROM data) SELECT * FROM a",
        "SELECT (SELECT COUNT(*) FROM data) FROM data",
        "SELECT * FROM data JOIN data b ON true",
        "SELECT ( FROM data",
    ],
)
def test_policy_failures_never_trigger_repair_or_execution(sql):
    provider = RepairProvider(first=sql)
    run = make_run(provider, lambda _: pytest.fail("Rejected SQL executed"))
    with pytest.raises(AppError) as error:
        execute_analysis_graph(run)
    assert error.value.detail["code"] == "unsafe_sql" and provider.repairs == 0


@pytest.mark.parametrize("second", ["SELECT missing_again FROM data", "DROP TABLE data"])
def test_bad_repair_is_rejected_without_third_generation(second):
    run = make_run(RepairProvider(second=second), lambda _: pytest.fail("Unsafe repair executed"))
    with pytest.raises(AppError):
        execute_analysis_graph(run)
    assert run.repairs == 1 and run.sql_calls == 2


@pytest.mark.parametrize(
    "code", ["query_timeout", "result_too_large", "query_busy", "query_failed"]
)
def test_execution_failures_are_terminal(code):
    def execute(_):
        raise AppError(422, code, "Synthetic failure")

    provider = RepairProvider(first="SELECT * FROM data")
    with pytest.raises(AppError) as error:
        execute_analysis_graph(make_run(provider, execute))
    assert error.value.detail["code"] == code and provider.repairs == 0


def test_explicit_repair_disable_and_workflow_deadline():
    provider = RepairProvider()
    with pytest.raises(AppError):
        execute_analysis_graph(make_run(provider, sql_repair_enabled=False))
    assert provider.repairs == 0
    run = make_run(provider)
    run.deadline = 0
    with pytest.raises(AppError) as error:
        execute_analysis_graph(run)
    assert error.value.detail["code"] == "workflow_timeout" and run.nodes == []


def test_shell_tracing_flag_cannot_export_graph_inputs(monkeypatch):
    sent = []
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr("langsmith.Client.create_run", lambda *args, **kwargs: sent.append(kwargs))
    monkeypatch.setattr("langsmith.Client.update_run", lambda *args, **kwargs: None)
    run = make_run(RepairProvider(first="SELECT * FROM data"))
    execute_analysis_graph(run)
    assert sent == []
