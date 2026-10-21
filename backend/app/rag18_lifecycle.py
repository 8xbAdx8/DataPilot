"""Real synthetic lifecycle acceptance, isolated transaction and explicit opt-in."""

import json
import os
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth import COOKIE, token_hash
from app.config import ROOT, Settings, get_settings
from app.db import get_session
from app.embeddings import CompatibleEmbeddingProvider
from app.evaluate import dependency
from app.main import app
from app.models import AuthSession, KnowledgeChunk
from app.rag18_evaluate import reviewed, write
from app.rag_evaluate import EmbeddingObservation, counts, seed


def run(output):
    if os.environ.get("DATAPILOT_RUN_REAL_EMBEDDING") != "1":
        raise ValueError("Explicit real opt-in required")
    original = Settings()
    if original.embedding_provider in {"none", "fake"}:
        raise ValueError("Real provider required")
    write(output, {"state": "claimed"}, exclusive=True)
    corpus, _ = reviewed()
    # One isolated empty owned dataset; no ranking corpus changes.
    corpus = {**corpus, "documents": [], "datasets": {"support": corpus["datasets"]["support"]}}
    url = make_url(original.database_url).set(host="127.0.0.1", database="datapilot_test")
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    observation, report = EmbeddingObservation(), {"state": "running", "checks": {}}
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / "data/runtime", prefix="rag18-life-") as temp:
            settings = original.model_copy(
                update={
                    "data_dir": Path(temp),
                    "datapilot_langsmith_enabled": False,
                    "knowledge_requests_per_window": 100,
                }
            )
            with engine.connect() as connection:
                before = counts(connection)
                connection.rollback()
                transaction = connection.begin()
                try:
                    with Session(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    ) as session:
                        seed(session, corpus, settings)
                        app.dependency_overrides[get_session] = dependency(session)
                        app.dependency_overrides[get_settings] = dependency(settings)

                        def login(client, owner):
                            token = secrets.token_urlsafe(32)
                            session.add(
                                AuthSession(
                                    token_hash=token_hash(token),
                                    user_id=corpus["owners"][owner],
                                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                                )
                            )
                            session.commit()
                            client.cookies.set(COOKIE, token)

                        with patch.object(
                            CompatibleEmbeddingProvider,
                            "embed",
                            lambda provider, texts: observation.embed(provider, texts),
                        ):
                            with TestClient(app, headers={"X-DataPilot-CSRF": "1"}) as client:
                                login(client, "support-owner")
                                target = "/api/datasets/" + corpus["datasets"]["support"]["id"]
                                body = {
                                    "title": "生命周期合成测试",
                                    "source_type": "metric_definition",
                                    "content": "合成生命周期指标=COUNT(*)，版本一。",
                                }
                                created = client.post(target + "/knowledge", json=body)
                                saved = created.json()
                                assert created.status_code == 201 and saved["status"] == "ready"
                                assert saved["chunk_count"] == 1 and not saved["fake_embedding"]
                                report["checks"]["create_real_persisted"] = True
                                path = "/api/knowledge/" + saved["id"]
                                first_chunk = session.scalar(
                                    select(KnowledgeChunk.id).where(
                                        KnowledgeChunk.document_id == saved["id"]
                                    )
                                )
                                before_duplicate = len(observation.requests)
                                duplicate = client.post(target + "/knowledge", json=body)
                                assert duplicate.status_code == 200
                                assert duplicate.json()["id"] == saved["id"]
                                assert duplicate.json()["duplicate"]
                                assert before_duplicate == len(observation.requests)
                                report["checks"]["duplicate_no_embedding"] = True
                                updated = client.put(
                                    path,
                                    json={
                                        **body,
                                        "content": "合成生命周期指标=SUM(handle_minutes)，版本二。",
                                    },
                                )
                                current = updated.json()
                                assert updated.status_code == 200 and current["status"] == "ready"
                                assert current["version"] == saved["version"] + 1
                                chunks = session.scalars(
                                    select(KnowledgeChunk).where(
                                        KnowledgeChunk.document_id == saved["id"]
                                    )
                                ).all()
                                assert len(chunks) == 1 and chunks[0].id != first_chunk
                                assert chunks[0].content == current["content"]
                                report["checks"]["update_replaced_vector_and_content"] = True
                                login(client, "outsider")
                                assert client.get(path).status_code == 404
                                assert (
                                    client.post(
                                        target + "/knowledge/search", json={"query": body["title"]}
                                    ).status_code
                                    == 404
                                )
                                report["checks"]["cross_owner_404"] = True
                                login(client, "support-owner")
                                assert client.delete(path).status_code == 204
                                assert client.get(path).status_code == 404
                                assert (
                                    session.scalar(
                                        select(func.count())
                                        .select_from(KnowledgeChunk)
                                        .where(KnowledgeChunk.document_id == saved["id"])
                                    )
                                    == 0
                                )
                                found = client.post(
                                    target + "/knowledge/search", json={"query": body["title"]}
                                ).json()
                                assert found["status"] == "no_documents" and not found["citations"]
                                report["checks"]["delete_zero_chunks_no_documents"] = True
                finally:
                    transaction.rollback()
                    app.dependency_overrides.clear()
                report["database_rollback_verified"] = counts(connection) == before
                assert report["database_rollback_verified"]
        report["state"] = "complete"
    except Exception as error:
        report["state"], report["failure_class"] = "failed", type(error).__name__
        raise
    finally:
        report["embedding_requests"] = observation.requests
        write(output, report)
        engine.dispose()
    return report


if __name__ == "__main__":
    try:
        print(json.dumps({"state": run(ROOT / "docs/rag18-lifecycle-v1.json")["state"]}))
    except Exception as error:
        print(json.dumps({"state": "failed", "failure_class": type(error).__name__}))
        raise SystemExit(1) from None
