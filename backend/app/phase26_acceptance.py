"""Real lifecycle API/pgvector trial. Synthetic knowledge, shared budget, no chat."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langsmith import tracing_context
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth import COOKIE, token_hash
from app.config import ROOT, Settings, get_settings
from app.db import get_session
from app.embeddings import CompatibleEmbeddingProvider
from app.errors import AppError
from app.main import app
from app.models import AuthSession, KnowledgeChunk, KnowledgeDocument, User
from app.phase25_acceptance import LIVE, TOKENIZER, authorization
from app.rag24_budget import BudgetStop
from app.rag24_evaluate import temporary_database, write_new
from app.rag_evaluate import counts
from app.trial_budget import InputEstimator, MeteredTransport, TrialLedger

PRIMARY = [f"冷库签收规则：温度必须低于{n}度。" for n in (5, 3, 2, 1)]
PERSONAL = ["回收规定：封条必须为蓝色。", "回收规定：封条必须为绿色。"]


def engine_for_trial():
    public = Settings(_env_file=ROOT / ".env.example")
    return create_engine(
        make_url(public.database_url).set(host="127.0.0.1", port=15636),
        connect_args={"connect_timeout": 5},
    )


class LifecycleProvider:
    source = "provider"

    def __init__(self, settings, ledger):
        self.settings, self.ledger = settings, ledger
        original = CompatibleEmbeddingProvider(settings)
        self.model, self.space = original.model, original.space
        self.hook = None
        self.fail = False
        self.bad_dimension = False
        self.sent = 0

    def embed(self, texts):
        if self.hook:
            hook, self.hook = self.hook, None
            hook()
        if self.fail:
            self.fail = False
            raise AppError(503, "synthetic_index_failure", "受控索引失败，未发送真实HTTP。")
        if self.bad_dimension:
            self.bad_dimension = False
            return [[1, 2] for _ in texts]  # Explicit local fault, never a real vector.
        missing = []
        for value in texts:
            try:
                self.ledger.cache(self.space, [value])
            except BudgetStop as error:
                if str(error) != "trial_cache_miss_no_network_fallback":
                    raise
                missing.append(value)
        if missing:
            if any(value not in PRIMARY + PERSONAL for value in missing):
                raise BudgetStop("phase26_unapproved_synthetic_input")
            metered = MeteredTransport(
                self.ledger,
                InputEstimator(TOKENIZER),
                stage=26,
                kind="embedding",
                case="lifecycle",
                channel=f"index-{self.sent}",
                provider=self.settings.embedding_provider,
                model=self.model,
                base_url=self.settings.embedding_base_url,
                space=self.space,
            )
            CompatibleEmbeddingProvider(self.settings, transport=metered).embed(missing)
            self.sent += 1
        return self.ledger.cache(self.space, texts)


def run(round_name):
    if os.environ.get("RUN_LIVE_PHASE26") != "1":
        raise BudgetStop("phase26_opt_in_required")
    if not round_name.replace("-", "").isalnum():
        raise BudgetStop("phase26_invalid_round")
    output = ROOT / "docs/phase26" / round_name
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings().model_copy(
        update={
            "llm_provider": "mock",
            "llm_api_key": "",
            "datapilot_langsmith_enabled": False,
            "langsmith_api_key": "",
            "knowledge_requests_per_window": 120,
            "rag_evidence_policy": "legacy",
            "rag_min_similarity": 0.70,
            "rag_strategy": "vector",
            "rag_reranker": "none",
        }
    )
    if not settings.embedding_api_key or settings.embedding_model != "text-embedding-v4":
        raise BudgetStop("phase26_real_embedding_missing")
    ledger = TrialLedger(LIVE / "ledger.sqlite", authorization())
    provider = LifecycleProvider(settings, ledger)
    plan = InputEstimator(TOKENIZER).local.estimate(PRIMARY + PERSONAL)
    prior = [r for r in ledger.export()["requests"] if r["stage"] == 26]
    missing = []
    for value in PRIMARY + PERSONAL:
        try:
            ledger.cache(provider.space, [value])
        except BudgetStop as error:
            if str(error) != "trial_cache_miss_no_network_fallback":
                raise
            missing.append(value)
    pending_reserve = InputEstimator(TOKENIZER).local.estimate(missing)[1]
    if len(prior) + len(missing) > 8 or (
        sum(r["input_tokens"] or 0 for r in prior) + pending_reserve > 2000
    ):
        raise BudgetStop("phase26_whole_plan_does_not_fit")
    write_new(
        output / "plan.json",
        {
            "start_head": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
            "unique_approved_synthetic_texts": PRIMARY + PERSONAL,
            "planned_max_http": 6,
            "new_http_required": len(missing),
            "pending_token_reservation": pending_reserve,
            "local_estimate": plan[0],
            "conservative_reserve": plan[1],
            "budget_before": ledger.export(),
            "chat": 0,
            "judge": 0,
            "langsmith": 0,
            "faults": "controlled pre-HTTP faults and API interleaving, separately labelled",
            "source_sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT / "backend/app").glob("*.py")
            },
        },
    )
    base = engine_for_trial()
    records = []
    try:
        with base.connect() as c:
            before = counts(c)
        with temporary_database(base) as (engine, cleanup):
            with tempfile.TemporaryDirectory(dir=LIVE, prefix="phase26-") as directory:
                settings = settings.model_copy(update={"data_dir": Path(directory)})
                actors = {}
                with Session(engine) as session:
                    for key in ("reader", "other"):
                        user = User(
                            username="phase26-" + secrets.token_hex(8),
                            password_hash="unused-synthetic",
                        )
                        session.add(user)
                        session.flush()
                        token = secrets.token_urlsafe(32)
                        session.add(
                            AuthSession(
                                user_id=user.id,
                                token_hash=token_hash(token),
                                expires_at=datetime.now(UTC) + timedelta(hours=1),
                            )
                        )
                        actors[key] = (user.id, token)
                    session.commit()

                def sessions():
                    with Session(engine, expire_on_commit=False) as session:
                        yield session

                app.dependency_overrides[get_session] = sessions
                app.dependency_overrides[get_settings] = lambda: settings

                def snapshot():
                    with Session(engine) as session:
                        docs = session.scalars(
                            select(KnowledgeDocument).order_by(KnowledgeDocument.id)
                        ).all()
                        chunks = session.scalars(
                            select(KnowledgeChunk).order_by(KnowledgeChunk.id)
                        ).all()
                        return {
                            "documents": [
                                {
                                    "id": d.id,
                                    "owner_id": d.owner_id,
                                    "dataset_id": d.dataset_id,
                                    "version": d.version,
                                    "status": d.status,
                                    "content": d.content,
                                    "space": d.embedding_space,
                                }
                                for d in docs
                            ],
                            "chunks": [
                                {
                                    "id": c.id,
                                    "document_id": c.document_id,
                                    "content": c.content,
                                    "dimensions": len(c.embedding),
                                    "space": c.embedding_space,
                                    "model": c.embedding_model,
                                }
                                for c in chunks
                            ],
                        }

                def record(label, response=None, **extra):
                    row = {
                        "id": label,
                        "http_status": response.status_code if response is not None else None,
                        "response": response.json()
                        if response is not None and response.content
                        else None,
                        "independent_connection": snapshot(),
                        **extra,
                    }
                    records.append(row)
                    write_new(output / f"{len(records):02d}-{label}.json", row)

                with (
                    patch("app.knowledge.get_embedding_provider", lambda _: provider),
                    patch("app.embeddings.get_embedding_provider", lambda _: provider),
                    tracing_context(enabled=False),
                    TestClient(
                        app, raise_server_exceptions=False, headers={"X-DataPilot-CSRF": "1"}
                    ) as client,
                ):
                    client.cookies.set(COOKIE, actors["reader"][1])
                    targets = {}
                    for key in ("target", "sibling"):
                        response = client.post(
                            "/api/datasets", files={"file": ("synthetic.csv", b"value\n1\n")}
                        )
                        assert response.status_code == 201
                        targets[key] = response.json()["id"]
                    client.cookies.set(COOKIE, actors["other"][1])
                    other = client.post(
                        "/api/datasets", files={"file": ("synthetic.csv", b"value\n1\n")}
                    ).json()["id"]
                    client.cookies.set(COOKIE, actors["reader"][1])
                    route = f"/api/datasets/{targets['target']}/knowledge"

                    def body(content, title="冷库签收规则"):
                        return {"title": title, "source_type": "business_rule", "content": content}

                    def search(content, target=None):
                        response = client.post(
                            f"/api/datasets/{target or targets['target']}/knowledge/search",
                            json={"query": content},
                        )
                        assert response.status_code == 200, response.text
                        result = response.json()
                        saved = snapshot()
                        for source in result["citations"]:
                            doc = next(
                                d for d in saved["documents"] if d["id"] == source["document_id"]
                            )
                            chunk = next(
                                c for c in saved["chunks"] if c["id"] == source["chunk_id"]
                            )
                            assert doc["owner_id"] == actors["reader"][0] and doc["dataset_id"] in {
                                None,
                                target or targets["target"],
                            }
                            assert (
                                doc["status"] == "ready"
                                and chunk["content"][:800] == source["excerpt"]
                            )
                            assert (
                                chunk["dimensions"] == 1536
                                and doc["space"] == chunk["space"] == provider.space
                            )
                        return response

                    response = client.post(route, json=body(PRIMARY[0]))
                    assert response.status_code == 201
                    first = response.json()
                    docid = first["id"]
                    old_chunk = snapshot()["chunks"][0]["id"]
                    record("created-ready", response)
                    assert first["version"] == 1
                    duplicate = client.post(route, json=body(PRIMARY[0]))
                    assert duplicate.status_code == 200 and duplicate.json()["id"] == docid
                    record("deduplicated", duplicate)
                    found = search(PRIMARY[0])
                    assert any(s["document_id"] == docid for s in found.json()["citations"])
                    record("initial-reference", found)

                    def pending():
                        state = snapshot()
                        assert (
                            next(d for d in state["documents"] if d["id"] == docid)["status"]
                            == "pending"
                        )
                        result = search(PRIMARY[0])
                        assert result.json()["citations"] == []
                        record("pending-hides-old-vectors", result, controlled_interleaving=True)

                    provider.hook = pending
                    updated = client.put(f"/api/knowledge/{docid}", json=body(PRIMARY[1]))
                    assert updated.json()["status"] == "ready" and updated.json()["version"] == 2
                    assert old_chunk not in {c["id"] for c in snapshot()["chunks"]}
                    record("updated-version-two", updated)
                    fresh = search(PRIMARY[1])
                    assert all(s["excerpt"] == PRIMARY[1] for s in fresh.json()["citations"])
                    record("new-body-only", fresh)
                    provider.fail = True
                    failed = client.post(f"/api/knowledge/{docid}/reindex")
                    assert failed.json()["status"] == "failed"
                    record("controlled-index-failure", failed, external_http_for_fault=0)
                    hidden = search(PRIMARY[1])
                    assert hidden.json()["citations"] == []
                    record("failed-is-not-ready", hidden)
                    recovered = client.post(f"/api/knowledge/{docid}/reindex")
                    assert recovered.json()["status"] == "ready"
                    record("recovered-from-cache", recovered)
                    provider.bad_dimension = True
                    invalid = client.post(f"/api/knowledge/{docid}/reindex")
                    assert invalid.json()["status"] == "failed"
                    record("controlled-invalid-dimension", invalid, external_http_for_fault=0)
                    assert (
                        client.post(f"/api/knowledge/{docid}/reindex").json()["status"] == "ready"
                    )

                    def supersede():
                        nested = client.put(f"/api/knowledge/{docid}", json=body(PRIMARY[3]))
                        assert nested.status_code == 200 and nested.json()["status"] == "ready"
                        record(
                            "concurrent-newer-version-wins", nested, controlled_interleaving=True
                        )

                    provider.hook = supersede
                    stale = client.put(f"/api/knowledge/{docid}", json=body(PRIMARY[2]))
                    assert stale.status_code == 409, stale.text
                    record("stale-index-discarded", stale)
                    assert (
                        next(d for d in snapshot()["documents"] if d["id"] == docid)["content"]
                        == PRIMARY[3]
                    )
                    assert {
                        c["content"] for c in snapshot()["chunks"] if c["document_id"] == docid
                    } == {PRIMARY[3]}
                    previous_version = next(
                        d["version"] for d in snapshot()["documents"] if d["id"] == docid
                    )
                    same_body = client.put(f"/api/knowledge/{docid}", json=body(PRIMARY[3]))
                    assert same_body.json()["content"] == PRIMARY[3]
                    assert same_body.json()["version"] == previous_version + 1
                    assert {
                        c["content"] for c in snapshot()["chunks"] if c["document_id"] == docid
                    } == {PRIMARY[3]}
                    record("same-body-update-monotonic-version", same_body)
                    removed = client.delete(f"/api/knowledge/{docid}")
                    assert removed.status_code == 204
                    assert client.delete(f"/api/knowledge/{docid}").status_code == 404
                    assert all(c["document_id"] != docid for c in snapshot()["chunks"])
                    record("deleted-no-vectors", removed)
                    no_match = search(PRIMARY[3])
                    assert no_match.json()["citations"] == []
                    record("deleted-not-retrievable", no_match)
                    recreated = client.post(route, json=body(PRIMARY[3]))
                    assert recreated.status_code == 201 and recreated.json()["id"] != docid
                    new_id = recreated.json()["id"]
                    record("same-name-new-identity", recreated)

                    personal = client.post("/api/knowledge", json=body(PERSONAL[0], "回收规定"))
                    assert personal.status_code == 201 and personal.json()["dataset_id"] is None
                    pid = personal.json()["id"]
                    record("personal-api-create", personal)
                    allowed = search(PERSONAL[0], targets["sibling"])
                    assert {s["document_id"] for s in allowed.json()["citations"]} == {pid}
                    record("personal-shared-only-with-owner", allowed)
                    client.cookies.set(COOKIE, actors["other"][1])
                    for verb, url in [
                        ("get", f"/api/knowledge/{pid}"),
                        ("delete", f"/api/knowledge/{pid}"),
                        ("post", f"/api/knowledge/{pid}/reindex"),
                    ]:
                        denied = getattr(client, verb)(url)
                        assert denied.status_code == 404
                        record("foreign-personal-" + verb, denied)
                    denied = client.put(f"/api/knowledge/{pid}", json=body(PERSONAL[1], "回收规定"))
                    assert denied.status_code == 404
                    record("foreign-personal-update", denied)
                    hidden = client.post(
                        f"/api/datasets/{other}/knowledge/search", json={"query": PERSONAL[0]}
                    )
                    assert hidden.json()["citations"] == []
                    record("foreign-personal-not-recalled", hidden)
                    client.cookies.set(COOKIE, actors["reader"][1])
                    pupdate = client.put(
                        f"/api/knowledge/{pid}", json=body(PERSONAL[1], "回收规定")
                    )
                    assert pupdate.json()["version"] == 2
                    record("personal-new-version", pupdate)
                    assert client.delete(f"/api/knowledge/{pid}").status_code == 204
                    # Delete during a cached real-vector reindex: no extra vendor call.
                    provider.hook = lambda: client.delete(f"/api/knowledge/{new_id}")
                    deleted_race = client.post(f"/api/knowledge/{new_id}/reindex")
                    assert deleted_race.status_code == 404
                    assert snapshot()["documents"] == [] and snapshot()["chunks"] == []
                    record(
                        "delete-during-reindex-no-resurrection",
                        deleted_race,
                        controlled_interleaving=True,
                    )
                    assert ledger.export()["usage_complete"] and ledger.export()["stopped"] is None
        with base.connect() as c:
            after = counts(c)
        assert before == after and cleanup["database_removed"]
        result = {
            "status": "PASS",
            "api_checkpoints": len(records),
            "persisted_checks": "independent DB connection after every API response",
            "state_machine": (
                "pending -> ready | failed; updates/reindex increment version; "
                "delete terminal; same-name create new ID"
            ),
            "faults": "controlled pre-HTTP faults/API interleaving; not vendor outage evidence",
            "ledger": ledger.export(),
            "cleanup": cleanup,
            "base_counts_unchanged": True,
            "chat_http": 0,
            "langsmith_http": 0,
            "idempotency": (
                "duplicate POST same ID; repeated DELETE404 same absence; "
                "reindex increments version, not version-idempotent"
            ),
            "history_snapshots": "retained owner audit; no longer live retrieval/execute authority",
        }
        write_new(output / "summary.json", result)
        return {k: v for k, v in result.items() if k != "ledger"}
    except Exception as failure:
        write_new(
            output / "failure.json",
            {
                "status": "stopped",
                "type": type(failure).__name__,
                "completed_checkpoints": len(records),
                "ledger": ledger.export(),
            },
        )
        raise
    finally:
        ledger.close()
        base.dispose()
        app.dependency_overrides.clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", default="lifecycle-01")
    try:
        print(json.dumps(run(parser.parse_args().round), ensure_ascii=True))
    except Exception as failure:
        print(
            json.dumps(
                {
                    "status": "stopped",
                    "type": type(failure).__name__,
                    "reason": str(failure)
                    if isinstance(failure, BudgetStop)
                    else "details_suppressed",
                }
            )
        )
        raise SystemExit(2) from None
