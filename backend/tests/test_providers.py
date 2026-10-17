import json

import httpx
import pytest

from app.config import Settings
from app.errors import AppError
from app.providers import CompatibleProvider, MockProvider

FIELDS = [{"name": "region", "type": "VARCHAR"}, {"name": "revenue", "type": "BIGINT"}]


def settings():
    return Settings(
        _env_file=None, llm_provider="openai_compatible", llm_api_key="test-only-placeholder"
    )


def test_mock_is_deterministic_and_schema_aware():
    provider = MockProvider()
    first = provider.generate("按 region 统计 revenue 总和", FIELDS)
    assert first == provider.generate("按 region 统计 revenue 总和", FIELDS)
    assert 'SUM("revenue")' in first.sql
    assert 'GROUP BY "region"' in first.sql
    assert "COUNT(*)" in provider.generate("多少行", FIELDS).sql
    with pytest.raises(AppError):
        provider.generate("预测明年收入", FIELDS)


def test_real_provider_does_not_fall_back_when_key_is_missing():
    config = settings()
    config.llm_api_key = ""
    with pytest.raises(AppError) as caught:
        CompatibleProvider(config).generate("行数", FIELDS)
    assert caught.value.detail["code"] == "llm_not_configured"


def test_compatible_http_contract_and_strict_json():
    def handle(request):
        assert request.url.path == "/chat/completions"
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        user = json.loads(payload["messages"][1]["content"])
        assert user["schema"] == FIELDS
        assert "preview" not in user
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"sql": "SELECT COUNT(*) FROM data", "explanation": "Count rows"}
                            )
                        }
                    }
                ]
            },
        )

    result = CompatibleProvider(settings(), httpx.MockTransport(handle)).generate("行数", FIELDS)
    assert result.sql == "SELECT COUNT(*) FROM data"


@pytest.mark.parametrize(
    "content",
    [
        "```json\n{}\n```",
        "not JSON",
        "{}",
        '{"sql":123,"explanation":"x"}',
        '{"sql":"SELECT * FROM data","explanation":"x","extra":"field"}',
    ],
)
def test_malformed_model_responses(content):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    with pytest.raises(AppError) as caught:
        CompatibleProvider(settings(), transport).generate("行数", FIELDS)
    assert caught.value.detail["code"] == "llm_invalid_response"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_http_errors_do_not_expose_provider_body(status):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status, text="sensitive upstream detail")
    )
    with pytest.raises(AppError) as caught:
        CompatibleProvider(settings(), transport).generate("行数", FIELDS)
    assert caught.value.detail["code"] == "llm_http_error"
    assert "sensitive" not in str(caught.value.detail)


def test_llm_timeout():
    def timeout(_):
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(AppError) as caught:
        CompatibleProvider(settings(), httpx.MockTransport(timeout)).generate("行数", FIELDS)
    assert caught.value.detail["code"] == "llm_timeout"
