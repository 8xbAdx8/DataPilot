import multiprocessing as mp
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from app.errors import AppError
from app.execution import execute_query
from app.sql_guard import validate_sql


@pytest.fixture
def data_path(tmp_path):
    path = tmp_path / "data.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE data AS SELECT 'East' AS region, 10 AS revenue")
        connection.execute("INSERT INTO data VALUES ('West',20), ('East',30)")
    return path


def approved(sql):
    return validate_sql(
        sql, [{"name": "region", "type": "VARCHAR"}, {"name": "revenue", "type": "INTEGER"}]
    )


def test_real_duckdb_query(data_path, settings):
    result = execute_query(data_path, approved("SELECT SUM(revenue) AS total FROM data"), settings)
    assert result["rows"] == [{"total": 60}]
    assert result["truncated"] is False


def test_empty_and_row_limited_results(data_path, settings):
    empty = execute_query(data_path, approved("SELECT * FROM data WHERE revenue < 0"), settings)
    assert empty["rows"] == []
    assert len(empty["columns"]) == 2
    settings.max_result_rows = 2
    result = execute_query(data_path, approved("SELECT * FROM data ORDER BY revenue"), settings)
    assert len(result["rows"]) == 2
    assert result["truncated"] is True


def test_result_byte_limit(data_path, settings):
    settings.max_result_bytes = 10
    with pytest.raises(AppError) as caught:
        execute_query(data_path, approved("SELECT * FROM data"), settings)
    assert caught.value.detail["code"] == "result_too_large"


def test_deadline_terminates_worker(data_path, settings):
    settings.query_timeout_seconds = 0.001
    before = {child.pid for child in mp.active_children()}
    with pytest.raises(AppError) as caught:
        execute_query(data_path, approved("SELECT * FROM data"), settings)
    assert caught.value.detail["code"] == "query_timeout"
    assert {child.pid for child in mp.active_children()} == before


def test_worker_blocks_filesystem_even_if_guard_is_accidentally_skipped(data_path, settings):
    # Defense in depth: direct call deliberately bypasses AST in this regression test only.
    with pytest.raises(AppError) as caught:
        execute_query(data_path, "SELECT * FROM read_csv('/etc/passwd')", settings)
    assert caught.value.detail["code"] == "query_failed"


def test_real_cpu_bound_query_is_terminated(data_path, settings):
    # Internal executor test deliberately bypasses the guard to force sustained computation.
    # The production route rejects range() before execution.
    settings.query_timeout_seconds = 1.5
    with pytest.raises(AppError) as caught:
        execute_query(data_path, "SELECT SUM(i) FROM range(1000000000000) AS t(i)", settings)
    assert caught.value.detail["code"] == "query_timeout"
    assert not mp.active_children()


def test_timeout_reaps_the_actual_subprocess(data_path, settings, monkeypatch):
    from app import execution

    started = []
    original = execution.subprocess.Popen

    def tracked(*args, **kwargs):
        process = original(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(execution.subprocess, "Popen", tracked)
    settings.query_timeout_seconds = 0.001
    with pytest.raises(AppError) as caught:
        execute_query(data_path, approved("SELECT * FROM data"), settings)
    assert caught.value.detail["code"] == "query_timeout"
    assert len(started) == 1 and started[0].poll() is not None
    # A killed request must return its slot; the next real query succeeds.
    settings.query_timeout_seconds = 5
    assert execute_query(data_path, approved("SELECT COUNT(*) AS n FROM data"), settings)[
        "rows"
    ] == [{"n": 3}]


def test_worker_ignores_pythonpath_and_does_not_inherit_credentials(
    data_path, settings, monkeypatch, tmp_path
):
    from app import execution

    marker = tmp_path / "unexpected.txt"
    (tmp_path / "sitecustomize.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('unsafe')", encoding="utf-8"
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("SYNTHETIC_API_KEY", "never-inherit-placeholder")
    monkeypatch.setenv("DATABASE_URL", "never-inherit-placeholder")
    original = execution.subprocess.Popen

    def checked(*args, **kwargs):
        assert "SYNTHETIC_API_KEY" not in kwargs["env"]
        assert "DATABASE_URL" not in kwargs["env"]
        return original(*args, **kwargs)

    monkeypatch.setattr(execution.subprocess, "Popen", checked)
    assert execute_query(data_path, approved("SELECT SUM(revenue) AS total FROM data"), settings)[
        "rows"
    ] == [{"total": 60}]
    assert not marker.exists()


def test_heavy_parent_entry_is_not_replayed_in_query_worker(data_path, tmp_path):
    launcher = tmp_path / "heavy_entry.py"
    # The old spawn executor re-imports __main__ as __mp_main__; this is a
    # deterministic regression for the measured 5.68s Web/Agent import overhead.
    launcher.write_text(
        "if __name__ != '__main__':\n    raise RuntimeError('Parent entry replayed')\n"
        "from app.main import app\n"
        "from pathlib import Path\n"
        "from app.execution import execute_query\n"
        "from app.config import Settings\n"
        f"r=execute_query(Path({str(data_path)!r}), 'SELECT SUM(revenue) AS total FROM data', "
        "Settings(_env_file=None))\n"
        "assert r['rows'] == [{'total':60}]\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])}
    completed = subprocess.run(
        [sys.executable, str(launcher)], env=env, capture_output=True, timeout=30
    )
    assert completed.returncode == 0, "Heavy-entry execution must complete without main replay"


def test_difference_of_sums_is_not_sum_of_differences(tmp_path, settings):
    path = tmp_path / "nulls.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE data(amount INTEGER, cost INTEGER)")
        con.execute("INSERT INTO data VALUES (10,3),(NULL,4)")
    fields = [{"name": name, "type": "INTEGER"} for name in ("amount", "cost")]
    sql = validate_sql(
        "SELECT SUM(amount)-SUM(cost) AS difference, SUM(amount-cost) AS row_total FROM data",
        fields,
    )
    assert execute_query(path, sql, settings)["rows"] == [{"difference": 3, "row_total": 7}]
