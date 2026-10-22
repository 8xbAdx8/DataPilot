import json
import subprocess

import pytest

from app import reranking
from app.errors import AppError
from app.retrieval import lexical_terms


@pytest.mark.postgres
@pytest.mark.parametrize("strategy", ["vector", "hybrid"])
def test_permission_first_candidate_search_and_semantic_gate(
    client, session, settings, auth_user, strategy
):
    from sqlalchemy import event

    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("Native lexical/vector queries require PostgreSQL")
    settings.embedding_provider = "fake"
    settings.rag_strategy = strategy
    settings.rag_min_similarity = 0.05

    def upload():
        return client.post(
            "/api/datasets", files={"file": ("synthetic.csv", b"revenue\n10\n20\n")}
        ).json()["id"]

    target, sibling = upload(), upload()
    body = {
        "title": "sales revenue",
        "content": "sales revenue total SUM revenue",
        "source_type": "metric_definition",
    }
    own = client.post(f"/api/datasets/{target}/knowledge", json=body).json()
    other = client.post(f"/api/datasets/{sibling}/knowledge", json=body).json()
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if "authorized_knowledge" in statement:
            statements.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", observe)
    try:
        found = client.post(
            f"/api/datasets/{target}/knowledge/search", json={"query": "sales revenue total"}
        ).json()
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", observe)
    assert found["status"] == "retrieved"
    assert {r["document_id"] for r in found["citations"]} == {own["id"]}
    assert all(r["document_id"] != other["id"] for r in found["citations"])
    if session.get_bind().dialect.name == "postgresql":
        assert statements and all("MATERIALIZED" in sql for sql in statements)
        assert all("owner_id" in sql and "dataset_id" in sql for sql in statements)
        assert len({r["chunk_id"] for r in found["citations"]}) == len(found["citations"])
        settings.rag_min_similarity = 1
        empty = client.post(
            f"/api/datasets/{target}/knowledge/search", json={"query": "unknown unrelated words"}
        ).json()
        assert empty["status"] == "no_match"


def test_lexical_terms_bounded_and_values_not_sql():
    terms = lexical_terms("销售额 revenue'; DROP TABLE data; --" * 100)
    assert "销售" in terms and "revenue" in terms
    assert len(terms) <= 64
    assert all("'" not in t and ";" not in t for t in terms)


@pytest.mark.parametrize(
    "kind,code",
    [
        ("timeout", "reranker_timeout"),
        ("failure", "reranker_failed"),
        ("foreign", "reranker_failed"),
        ("nan", "reranker_failed"),
    ],
)
def test_reranker_failures_never_silently_supply_unranked_sources(
    settings, monkeypatch, kind, code
):
    settings.rag_reranker = "flashrank"
    settings.reranker_cache_dir = settings.data_dir / "ranker"
    directory = settings.reranker_cache_dir / reranking.MODEL
    directory.mkdir(parents=True)
    (directory / "flashrank-MultiBERT-L12_Q.onnx").touch()
    passages = [{"chunk_id": "allowed", "title": "title", "excerpt": "synthetic"}]

    def run(*args, **kwargs):
        assert kwargs["timeout"] <= 60
        if kind == "timeout":
            raise subprocess.TimeoutExpired("ranker", 1)
        if kind == "failure":
            raise subprocess.CalledProcessError(2, "ranker", stderr="private-error")
        return subprocess.CompletedProcess(
            "ranker",
            0,
            stdout=json.dumps(
                [{"id": "foreign" if kind == "foreign" else "allowed", "score": float("nan")}]
            ),
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(AppError) as raised:
        reranking.rerank("test", passages, settings)
    assert raised.value.detail["code"] == code
    assert "private-error" not in str(raised.value.detail)


def test_missing_optional_ranker_is_explicit_and_none_needs_no_model(settings):
    passages = [{"chunk_id": "allowed"}]
    assert reranking.rerank("test", passages, settings) is passages
    settings.rag_reranker = "flashrank"
    settings.reranker_cache_dir = settings.data_dir / "missing"
    with pytest.raises(AppError) as raised:
        reranking.rerank("test", passages, settings)
    assert raised.value.detail["code"] == "reranker_not_ready"
