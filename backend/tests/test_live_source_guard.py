import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from app.knowledge import KnowledgeInput, delete_document, reindex_document, update_document
from app.main import app
from app.models import KnowledgeChunk
from app.providers import SqlProposal, get_provider


@pytest.mark.parametrize("change", ["update", "delete", "reindex"])
def test_source_changed_after_planning_cannot_reach_execute(
    client, session, settings, auth_user, monkeypatch, change
):
    value = json.loads(
        (Path(__file__).parents[1] / "app/evals/phase25/development-v1.json").read_bytes()
    )
    settings.embedding_provider = "fake"
    settings.knowledge_requests_per_window = 120
    dataset = client.post(
        "/api/datasets",
        files={
            "file": (
                "services.csv",
                (Path(__file__).parents[1] / "app/evals/phase25/services.csv").read_bytes(),
            )
        },
    ).json()
    document = value["documents"][0]
    body = {k: document[k] for k in ("title", "source_type", "content")}
    doc = client.post(f"/api/datasets/{dataset['id']}/knowledge", json=body).json()
    chunk = session.scalar(select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc["id"]))
    source = {
        "chunk_id": chunk.id,
        "document_id": doc["id"],
        "chunk_index": 0,
        "title": doc["title"],
        "source_type": doc["source_type"],
        "excerpt": chunk.content,
        "similarity": 0.9,
    }
    monkeypatch.setattr(
        "app.analyses.retrieve_knowledge",
        lambda *a, **k: {
            "status": "retrieved",
            "source": "fake",
            "used": False,
            "citations": [source],
        },
    )

    class ChangingProposal:
        def generate_with_context(self, *args):
            if change == "delete":
                delete_document(UUID(doc["id"]), session, auth_user)
            elif change == "reindex":
                reindex_document(UUID(doc["id"]), session, settings, auth_user)
            else:
                revised = json.loads(body["content"])
                revised["expression"] = "SUM(fee)"
                update_document(
                    UUID(doc["id"]),
                    KnowledgeInput(**{**body, "content": json.dumps(revised, ensure_ascii=False)}),
                    session,
                    settings,
                    auth_user,
                )
            return SqlProposal(
                sql=value["cases"][0]["reference_sql"],
                explanation="offline stale-source regression",
            )

    app.dependency_overrides[get_provider] = lambda: ChangingProposal()
    executed = []
    monkeypatch.setattr(
        "app.analyses.execute_query",
        lambda *a, **k: (
            executed.append(True)
            or {
                "columns": [{"name": "net_amount", "type": "BIGINT"}],
                "rows": [{"net_amount": 25}],
                "truncated": False,
            }
        ),
    )
    response = client.post(
        f"/api/datasets/{dataset['id']}/analyses", json={"question": value["cases"][0]["query"]}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "evidence_source_stale"
    assert executed == []
