import os
import time

import pytest

from app.analysis_agent import PlannerContext, plan_analysis
from app.config import Settings


@pytest.mark.live_llm
def test_real_native_tool_calling_is_explicit_opt_in(client, session, auth_user):
    if os.environ.get("RUN_LIVE_AGENT") != "1":
        pytest.skip("Native tool API requires explicit RUN_LIVE_AGENT=1")
    settings = Settings()
    assert settings.llm_provider != "mock" and settings.llm_api_key
    dataset = client.post(
        "/api/datasets", files={"file": ("live-agent.csv", b"value\n10\n20\n")}
    ).json()
    context = PlannerContext(session, auth_user.id, dataset["id"], settings, time.monotonic() + 60)
    plan = plan_analysis(
        context, "请先调用 inspect_dataset_schema 获取真实字段，再规划 value 总和的单表分析。"
    )
    assert not plan.needs_clarification
    assert "inspect_dataset_schema" in context.tools_used
    assert 1 <= context.model_calls <= settings.agent_max_model_calls
