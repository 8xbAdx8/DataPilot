import json
from types import SimpleNamespace

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.embeddings import FakeEmbeddingProvider
from app.phase25_acceptance import corpus, one_case, seed
from app.providers import SqlProposal


class OfflinePlan(BaseChatModel):
    @property
    def _llm_type(self):
        return "explicit-offline-phase25-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        payload = json.loads(next(m.content for m in messages if isinstance(m, HumanMessage)))
        ids = [s["chunk_id"] for s in payload["untrusted_knowledge"]]
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "AnalysisPlan",
                                "id": "offline-only",
                                "args": {"knowledge_chunk_ids": ids},
                            }
                        ],
                    )
                )
            ]
        )


def test_reviewed_labels_are_bound_and_null_oracle_is_independent():
    value = corpus()
    assert len(value["cases"]) == 8 and len(value["documents"]) == 8
    assert value["cases"][0]["rows"] == [[(10 + 20) - (2 + 3)]]
    assert value["cases"][1]["rows"] == [[3]]


@pytest.mark.postgres
@pytest.mark.parametrize("unsafe", [None, "OR", "row_null", "alias_filter"])
def test_original_graph_contract_and_execute_boundary_with_offline_model(
    session, settings, monkeypatch, unsafe
):
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("actual pgvector requires PostgreSQL")
    value = corpus()
    monkeypatch.setattr("app.phase25_acceptance.InputEstimator", lambda _: SimpleNamespace())
    settings.embedding_provider = "fake"
    settings.agent_max_model_calls = 1
    settings.sql_repair_enabled = False
    settings.knowledge_requests_per_window = 120
    settings.rag_min_similarity = 0.0
    fake = FakeEmbeddingProvider()
    monkeypatch.setattr("app.knowledge.get_embedding_provider", lambda _: fake)
    monkeypatch.setattr("app.embeddings.get_embedding_provider", lambda _: fake)
    owner, targets, documents = seed(session, settings, value)
    case = value["cases"][0]
    sql = case["reference_sql"]
    if unsafe == "OR":
        sql += " OR 1=1"
    elif unsafe == "row_null":
        sql = sql.replace("SUM(fee)-SUM(cost)", "SUM(fee-cost)")
    elif unsafe == "alias_filter":
        sql = "SELECT SUM(fee)-SUM(cost) AS state FROM data WHERE state='closed' OR fee>0"
    monkeypatch.setattr(
        "app.phase25_acceptance.CompatibleProvider",
        lambda *a, **k: SimpleNamespace(
            generate_with_context=lambda *a, **k: SqlProposal(
                sql=sql, explanation="offline proposal"
            )
        ),
    )
    monkeypatch.setattr("app.phase25_acceptance.ChatOpenAI", lambda **k: OfflinePlan())
    # Does not send HTTP: SQL provider and native model are explicitly offline doubles.
    result = one_case(session, settings, None, case, owner, targets["target"], documents, None)
    if unsafe:
        assert result["error_code"] == "metric_contract_violation"
        assert result["execute_calls"] == 0
    else:
        assert result["assertions_passed"], {
            k: result[k] for k in ("status", "error_code", "adopted_sources", "result")
        }
        assert result["result"]["rows"] == [{"net_amount": 25}]
        assert result["expected_result"]["rows"] == [[25]]
        evidence = result["workflow"]["rag"]["evidence"]
        assert evidence["used_source_ids"] == result["workflow"]["plan"]["knowledge_chunk_ids"]


@pytest.mark.postgres
def test_structured_fact_reaches_plan_sql_and_actual_result_with_offline_model(
    session, settings, monkeypatch
):
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("actual pgvector requires PostgreSQL")
    value = corpus()
    monkeypatch.setattr("app.phase25_acceptance.InputEstimator", lambda _: SimpleNamespace())
    settings.embedding_provider = "fake"
    settings.agent_max_model_calls = 1
    settings.knowledge_requests_per_window = 120
    settings.rag_min_similarity = 0.0
    fake = FakeEmbeddingProvider()
    monkeypatch.setattr("app.knowledge.get_embedding_provider", lambda _: fake)
    monkeypatch.setattr("app.embeddings.get_embedding_provider", lambda _: fake)
    owner, targets, documents = seed(session, settings, value)
    case = value["cases"][1]
    monkeypatch.setattr(
        "app.phase25_acceptance.CompatibleProvider",
        lambda *a, **k: SimpleNamespace(
            generate_with_context=lambda *a, **k: SqlProposal(
                sql=case["reference_sql"], explanation="offline"
            )
        ),
    )
    monkeypatch.setattr("app.phase25_acceptance.ChatOpenAI", lambda **k: OfflinePlan())
    result = one_case(session, settings, None, case, owner, targets["target"], documents, None)
    assert result["assertions_passed"] and result["adopted_sources"] == ["status"]
    assert result["result"]["rows"] == [{"row_count": 3}]


@pytest.mark.postgres
def test_six_negative_cases_do_not_invoke_model_or_execute(session, settings, monkeypatch):
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("actual pgvector requires PostgreSQL")
    value = corpus()
    monkeypatch.setattr("app.phase25_acceptance.InputEstimator", lambda _: SimpleNamespace())
    settings.embedding_provider = "fake"
    settings.knowledge_requests_per_window = 120
    fake = FakeEmbeddingProvider()
    monkeypatch.setattr("app.knowledge.get_embedding_provider", lambda _: fake)
    monkeypatch.setattr("app.embeddings.get_embedding_provider", lambda _: fake)
    owner, targets, documents = seed(session, settings, value)
    monkeypatch.setattr(
        "app.phase25_acceptance.CompatibleProvider", lambda *a, **k: SimpleNamespace()
    )
    monkeypatch.setattr(
        "app.phase25_acceptance.ChatOpenAI", lambda **k: pytest.fail("no model on missing evidence")
    )
    for case in value["cases"][2:]:
        result = one_case(session, settings, None, case, owner, targets["target"], documents, None)
        assert result["assertions_passed"] and result["execute_calls"] == 0


@pytest.mark.postgres
def test_changed_version_is_rejected_before_execution(session, settings, monkeypatch):
    from app.errors import AppError
    from app.models import KnowledgeChunk, KnowledgeDocument
    from app.phase25_acceptance import check_current

    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("actual pgvector requires PostgreSQL")
    value = corpus()
    settings.embedding_provider = "fake"
    settings.knowledge_requests_per_window = 120
    fake = FakeEmbeddingProvider()
    monkeypatch.setattr("app.knowledge.get_embedding_provider", lambda _: fake)
    monkeypatch.setattr("app.embeddings.get_embedding_provider", lambda _: fake)
    owner, targets, documents = seed(session, settings, value)
    document = session.get(KnowledgeDocument, documents["margin"])
    chunk = session.query(KnowledgeChunk).filter_by(document_id=document.id).one()
    old = {"chunk_id": chunk.id, "excerpt": chunk.content[:800], "document_version": 1}
    document.version = 2
    session.flush()
    with pytest.raises(AppError) as rejected:
        check_current(session, owner, targets["target"], [old], {chunk.id})
    assert rejected.value.detail["code"] == "evidence_source_denied"
