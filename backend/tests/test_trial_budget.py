import json
from types import SimpleNamespace

import httpx
import pytest

from app.rag24_budget import BudgetStop
from app.trial_budget import MeteredTransport, TrialLedger


@pytest.fixture
def ledger(tmp_path):
    value = TrialLedger(
        tmp_path / "shared.sqlite", {"instruction": "synthetic-review"}, create=True
    )
    yield value
    value.close()


def reserve(ledger, stage=25, kind="chat", tokens=100):
    return ledger.reserve(stage, kind, "case", "agent", "synthetic", "model", {}, 10, tokens)


def complete(ledger, request_id, tokens=10):
    ledger.complete(
        request_id,
        {"usage": {"prompt_tokens": tokens, "completion_tokens": 2, "total_tokens": tokens + 2}},
        "synthetic-id",
        1,
    )


def test_stage_and_shared_budget_without_reset_or_reauthorization(ledger):
    for _ in range(12):
        complete(ledger, reserve(ledger))
    with pytest.raises(BudgetStop, match="preflight_denied"):
        reserve(ledger)
    for _ in range(18):
        complete(ledger, reserve(ledger, stage=29))
    assert ledger.export()["totals"]["chat"]["requests"] == 30
    with pytest.raises(BudgetStop):
        reserve(ledger, stage=29)
    with pytest.raises(FileExistsError):
        TrialLedger(ledger.path, {}, create=True)
    with pytest.raises(BudgetStop, match="authorization_mismatch"):
        TrialLedger(ledger.path, {"changed": True})


def test_uncertain_attempt_blocks_both_kinds_after_reopen(ledger):
    reserve(ledger)
    reopened = TrialLedger(ledger.path, {"instruction": "synthetic-review"})
    with pytest.raises(BudgetStop, match="uncertain"):
        reserve(reopened, kind="embedding")
    assert reopened.export()["requests"][0]["input_tokens"] is None
    reopened.close()


@pytest.mark.parametrize(
    "usage",
    [{}, {"prompt_tokens": 1}, {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 8}],
)
def test_missing_inconsistent_usage_stops_globally_without_zero(ledger, usage):
    key = reserve(ledger)
    with pytest.raises(BudgetStop):
        ledger.complete(key, {"usage": usage}, "id", 1)
    with pytest.raises(BudgetStop):
        reserve(ledger, kind="embedding")
    assert ledger.export()["totals"]["chat"]["unknown_requests"] == 1


def test_reservation_exceeded_keeps_actual_usage_then_stops(ledger):
    with pytest.raises(BudgetStop):
        complete(ledger, reserve(ledger, tokens=20), tokens=30)
    assert ledger.export()["requests"][0]["input_tokens"] == 30
    with pytest.raises(BudgetStop):
        reserve(ledger)


def test_stage_token_limit_checked_before_network_and_forbidden_stages(ledger):
    with pytest.raises(BudgetStop):
        reserve(ledger, tokens=8001)
    with pytest.raises(BudgetStop):
        reserve(ledger, stage=28)
    assert ledger.export()["requests"] == []


def test_transport_reserves_durably_and_uncertainty_cannot_retry(ledger):
    sent = []

    def broken(request):
        assert ledger.export()["requests"][0]["status"] == "reserved"
        sent.append(True)
        raise httpx.ReadTimeout("synthetic")

    transport = MeteredTransport(
        ledger,
        SimpleNamespace(estimate=lambda *_: (10, 100)),
        stage=25,
        kind="chat",
        case="case",
        channel="agent",
        provider="synthetic",
        model="model",
        base_url="https://test.invalid",
        inner=httpx.MockTransport(broken),
    )
    with httpx.Client(transport=transport) as client:
        for _ in range(2):
            with pytest.raises(BudgetStop):
                client.post("https://test.invalid/chat/completions", json={"model": "model"})
    assert len(sent) == 1
    assert ledger.export()["totals"]["chat"]["unknown_requests"] == 1


def test_no_redirect_route_stream_or_header_leaks(ledger):
    transport = MeteredTransport(
        ledger,
        SimpleNamespace(estimate=lambda *_: (10, 100)),
        stage=25,
        kind="chat",
        case="case",
        channel="agent",
        provider="synthetic",
        model="model",
        base_url="https://test.invalid",
        inner=httpx.MockTransport(lambda _: pytest.fail("must not send")),
    )
    with httpx.Client(transport=transport) as client:
        for url, body in [
            ("https://test.invalid/other", {}),
            ("https://test.invalid/chat/completions", {"model": "model", "stream": True}),
        ]:
            with pytest.raises(BudgetStop):
                client.post(url, json=body, headers={"Authorization": "Bearer synthetic-secret"})
    assert "synthetic-secret" not in json.dumps(ledger.export())
    assert not ledger.export()["requests"]


def test_native_graph_worker_thread_uses_same_durable_ledger(ledger):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        request_id = pool.submit(reserve, ledger).result()
        pool.submit(complete, ledger, request_id).result()
    assert ledger.export()["totals"]["chat"]["known_input_tokens"] == 10


def test_cached_vectors_cannot_be_billed_again_and_cache_is_read_only(ledger):
    from app.embeddings import FakeEmbeddingProvider

    payload = {"model": "model", "input": ["synthetic source"]}
    key = ledger.reserve(25, "embedding", "case", "index", "synthetic", "model", payload, 3, 20)
    ledger.complete(
        key,
        {"usage": {"prompt_tokens": 4, "total_tokens": 4}},
        "id",
        1,
        ("space", payload["input"], FakeEmbeddingProvider().embed(payload["input"])),
    )
    transport = MeteredTransport(
        ledger,
        SimpleNamespace(estimate=lambda *_: (3, 20)),
        stage=25,
        kind="embedding",
        case="case",
        channel="index",
        provider="synthetic",
        model="model",
        base_url="https://test.invalid",
        space="space",
        inner=httpx.MockTransport(lambda _: pytest.fail("cached vector must not send")),
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(BudgetStop, match="already_cached"):
            client.post("https://test.invalid/embeddings", json=payload)
    assert len(ledger.export()["requests"]) == 1
    assert len(ledger.cache("space", payload["input"])[0]) == 1536


def test_compressed_provider_reply_is_decoded_once_and_usage_is_recorded(ledger):
    import gzip

    body = {
        "id": "synthetic-id",
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }
    metered = MeteredTransport(
        ledger,
        SimpleNamespace(estimate=lambda *_: (10, 100)),
        stage=25,
        kind="chat",
        case="case",
        channel="agent",
        provider="synthetic",
        model="model",
        base_url="https://test.invalid",
        inner=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                content=gzip.compress(json.dumps(body).encode()),
            )
        ),
    )
    with httpx.Client(transport=metered) as client:
        assert (
            client.post("https://test.invalid/chat/completions", json={"model": "model"}).json()
            == body
        )
    assert ledger.export()["totals"]["chat"]["known_input_tokens"] == 10


def test_invalid_vector_keeps_available_usage_and_globally_stops(ledger):
    body = {
        "id": "id",
        "model": "model",
        "usage": {"prompt_tokens": 3, "total_tokens": 3},
        "data": [{"index": 0, "embedding": [1, 2]}],
    }
    metered = MeteredTransport(
        ledger,
        SimpleNamespace(estimate=lambda *_: (3, 30)),
        stage=25,
        kind="embedding",
        case="case",
        channel="index",
        provider="synthetic",
        model="model",
        space="space",
        base_url="https://test.invalid",
        inner=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    )
    with httpx.Client(transport=metered) as client:
        with pytest.raises(BudgetStop):
            client.post(
                "https://test.invalid/embeddings", json={"model": "model", "input": ["synthetic"]}
            )
    assert ledger.export()["requests"][0]["input_tokens"] == 3
    with pytest.raises(BudgetStop):
        reserve(ledger)
