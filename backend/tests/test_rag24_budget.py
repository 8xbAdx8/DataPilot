import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.config import ROOT
from app.metric21_acceptance import check_prior_usage
from app.providers import TokenUsage
from app.rag24_budget import BudgetStop, EmbeddingTransport, Ledger, embed_batch

AUTH = {"scope": "stage24-embedding-only", "historical_usage": "unknown", "requests": 80}
VECTOR = [1.0] + [0.0] * 1535
USAGE = TokenUsage(source="provider", prompt_tokens=5, total_tokens=5)


@pytest.fixture
def ledger(tmp_path):
    value = Ledger(tmp_path / "ledger.sqlite", AUTH, create=True)
    yield value
    value.close()


def test_previous_fixed_guard_is_unchanged_even_with_new_ledger(ledger):
    with pytest.raises(ValueError, match="Prior uncertain usage"):
        check_prior_usage(ROOT / "docs")
    assert ledger.export()["http_requests_reserved"] == 0


def test_completed_cache_is_durable_and_never_recounts_or_resends(ledger):
    request = ledger.reserve(["synthetic"], "development-1", 5, 50)
    ledger.finish(request, ["synthetic"], [VECTOR], USAGE, "synthetic-provider-id", 4)
    again = Ledger(ledger.path, AUTH)
    assert again.cache(["synthetic"]) == [VECTOR]
    assert again.export()["known_input_tokens"] == 5
    with pytest.raises(BudgetStop, match="duplicate"):
        again.reserve(["synthetic"], "development-2", 5, 50)
    assert again.export()["http_requests_reserved"] == 1
    with pytest.raises(FileExistsError):
        Ledger(ledger.path, AUTH, create=True)
    with pytest.raises(BudgetStop, match="authorization"):
        Ledger(ledger.path, {"scope": "other"})
    again.close()


def test_interrupted_request_blocks_replay_and_new_work(ledger):
    ledger.reserve(["one"], "first", 5, 50)
    recovered = Ledger(ledger.path, AUTH)
    with pytest.raises(BudgetStop, match="uncertain"):
        recovered.cache(["one"])
    with pytest.raises(BudgetStop, match="uncertain"):
        recovered.reserve(["two"], "second", 5, 50)
    recovered.close()


def test_atomic_reservation_serializes_concurrent_processes(ledger):
    another = Ledger(ledger.path, AUTH)
    ledger.reserve(["one"], "first", 5, 50)
    with pytest.raises(BudgetStop, match="uncertain"):
        another.reserve(["two"], "second", 5, 50)
    another.close()


def test_cache_uses_read_only_connection_in_api_worker_thread(ledger):
    request = ledger.reserve(["synthetic"], "first", 5, 50)
    ledger.finish(request, ["synthetic"], [VECTOR], USAGE, None, 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(ledger.cache, [["synthetic"], ["synthetic"]])) == [[VECTOR], [VECTOR]]
    assert ledger.export()["http_requests_reserved"] == 1


@pytest.mark.parametrize("case", ["usage", "over_reserve", "bad_dimension"])
def test_incomplete_or_inconsistent_reply_stops_further_calls(ledger, settings, case):
    settings.embedding_provider = "openai_compatible"
    settings.embedding_api_key = "synthetic-test-only"
    calls = []

    def response(request):
        calls.append(request)
        pending = (
            sqlite3.connect(ledger.path)
            .execute("SELECT COUNT(*) FROM requests WHERE status='reserved'")
            .fetchone()[0]
        )
        assert pending == 1  # Independently visible before actual HTTP dispatch.
        body = {
            "model": settings.embedding_model,
            "data": [{"index": 0, "embedding": VECTOR if case != "bad_dimension" else [1]}],
        }
        if case != "usage":
            body["usage"] = {
                "prompt_tokens": 500 if case == "over_reserve" else 5,
                "total_tokens": 500 if case == "over_reserve" else 5,
            }
        return httpx.Response(200, json=body, headers={"x-request-id": "controlled-id"})

    class Estimate:
        def estimate(self, _):
            return 5, 50

    with pytest.raises(BudgetStop, match="stopped"):
        embed_batch(
            ledger,
            settings,
            Estimate(),
            ["one"],
            "first",
            test_transport=httpx.MockTransport(response),
        )
    with pytest.raises(BudgetStop):
        embed_batch(
            ledger,
            settings,
            Estimate(),
            ["two"],
            "second",
            test_transport=httpx.MockTransport(response),
        )
    assert len(calls) == 1
    exported = ledger.export()
    assert not exported["usage_complete"] and exported["stopped"]
    if case == "over_reserve":
        assert exported["known_input_tokens"] == 500
    assert "synthetic-test-only" not in json.dumps(exported)


def test_http_error_and_timeout_have_no_retry(ledger, settings):
    settings.embedding_api_key = "synthetic-test-only"
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-error-sentinel", request=request)

    class Estimate:
        def estimate(self, _):
            return 5, 50

    with pytest.raises(BudgetStop, match="stopped"):
        embed_batch(
            ledger,
            settings,
            Estimate(),
            ["one"],
            "first",
            test_transport=httpx.MockTransport(timeout),
        )
    assert len(calls) == 1
    assert "private-error-sentinel" not in json.dumps(ledger.export())


def test_both_request_and_token_ceiling_checked_before_reserving(ledger):
    for i in range(80):
        request = ledger.reserve([str(i)], "batch", 5, 10)
        ledger.finish(request, [str(i)], [VECTOR], USAGE, None, 1)
    with pytest.raises(BudgetStop, match="budget_exceeded"):
        ledger.reserve(["81"], "batch", 5, 10)
    with pytest.raises(BudgetStop, match="budget_exceeded"):
        ledger.reserve(["huge"], "batch", 5, 20000)
    assert ledger.export()["http_requests_reserved"] == 80


def test_ledger_write_failure_prevents_dispatch(ledger, settings):
    ledger.db.execute("PRAGMA query_only=ON")

    class Estimate:
        def estimate(self, _):
            return 5, 50

    with pytest.raises(sqlite3.OperationalError):
        embed_batch(
            ledger,
            settings,
            Estimate(),
            ["one"],
            "first",
            test_transport=httpx.MockTransport(lambda _: pytest.fail("network dispatched")),
        )


def test_token_ceiling_is_independent_of_request_count(ledger):
    request = ledger.reserve(["one"], "first", 5, 50)
    ledger.finish(request, ["one"], [VECTOR], USAGE, None, 1)
    with pytest.raises(BudgetStop, match="budget_exceeded"):
        ledger.reserve(["huge"], "second", 5, 20000)
    assert ledger.export()["http_requests_reserved"] == 1


def test_final_ledger_failure_leaves_durable_uncertain_row(ledger, settings):
    settings.embedding_api_key = "synthetic-test-only"
    calls = []

    def response(request):
        calls.append(request)
        ledger.db.execute("PRAGMA query_only=ON")
        return httpx.Response(
            200,
            json={
                "model": settings.embedding_model,
                "data": [{"index": 0, "embedding": VECTOR}],
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            },
        )

    class Estimate:
        def estimate(self, _):
            return 5, 50

    with pytest.raises(sqlite3.OperationalError):
        embed_batch(
            ledger,
            settings,
            Estimate(),
            ["one"],
            "first",
            test_transport=httpx.MockTransport(response),
        )
    reopened = Ledger(ledger.path, AUTH)
    with pytest.raises(BudgetStop, match="uncertain"):
        reopened.reserve(["two"], "second", 5, 50)
    assert len(calls) == 1
    reopened.close()


def test_transport_disallows_chat_or_second_http():
    sent = []
    transport = EmbeddingTransport(
        "https://synthetic.invalid/v1",
        httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)),
    )
    with pytest.raises(BudgetStop, match="scope"):
        transport.handle_request(
            httpx.Request("POST", "https://synthetic.invalid/v1/chat/completions")
        )
    transport.handle_request(httpx.Request("POST", "https://synthetic.invalid/v1/embeddings"))
    with pytest.raises(BudgetStop, match="scope"):
        transport.handle_request(httpx.Request("POST", "https://synthetic.invalid/v1/embeddings"))
    assert len(sent) == 1
