import multiprocessing as mp

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
