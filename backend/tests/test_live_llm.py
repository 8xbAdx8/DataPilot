import os

import duckdb
import pytest

from app.config import Settings
from app.execution import execute_query
from app.providers import CompatibleProvider
from app.sql_guard import validate_sql


@pytest.mark.live_llm
def test_real_llm_generates_executable_sql(tmp_path):
    if os.environ.get("RUN_LIVE_LLM") != "1":
        pytest.skip("Real API is opt-in: RUN_LIVE_LLM=1 and explicit provider credentials")
    settings = Settings()
    assert settings.llm_provider != "mock" and settings.llm_api_key
    fields = [{"name": "value", "type": "INTEGER"}]
    path = tmp_path / "live.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE data(value INTEGER)")
        connection.execute("INSERT INTO data VALUES (10),(20),(30)")
    proposal = CompatibleProvider(settings).generate("计算数据总行数，仅返回一个计数字段", fields)
    sql = validate_sql(proposal.sql, fields)
    result = execute_query(path, sql, settings)
    assert len(result["rows"]) == 1
    assert list(result["rows"][0].values()) == [3]
