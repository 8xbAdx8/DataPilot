from fastapi.testclient import TestClient

from app.main import app


def test_personal_create_deduplicate_and_lifecycle_use_original_routes(client, settings):
    settings.embedding_provider = "fake"
    settings.knowledge_requests_per_window = 100
    body = {"title": "合成签收规则", "source_type": "business_rule", "content": "核验温度低于5度。"}
    created = client.post("/api/knowledge", json=body)
    assert created.status_code == 201, created.text
    doc = created.json()
    assert doc["dataset_id"] is None and doc["status"] == "ready" and doc["version"] == 1
    repeated = client.post("/api/knowledge", json=body)
    assert repeated.status_code == 200 and repeated.json()["id"] == doc["id"]
    assert client.post("/api/knowledge", json={**body, "owner_id": "forbidden"}).status_code == 422
    changed = client.put(
        f"/api/knowledge/{doc['id']}", json={**body, "content": "核验温度低于3度。"}
    )
    assert changed.json()["version"] == 2 and changed.json()["status"] == "ready"
    assert client.post(f"/api/knowledge/{doc['id']}/reindex").json()["version"] == 3
    assert client.delete(f"/api/knowledge/{doc['id']}").status_code == 204
    assert client.get(f"/api/knowledge/{doc['id']}").status_code == 404


def test_personal_create_still_requires_authentication_and_csrf(client, settings):
    settings.embedding_provider = "fake"
    body = {"title": "synthetic", "source_type": "business_rule", "content": "synthetic fact"}
    with TestClient(app) as untrusted:
        assert (
            untrusted.post(
                "/api/knowledge", json=body, headers={"X-DataPilot-CSRF": "1"}
            ).status_code
            == 401
        )
        untrusted.cookies.update(client.cookies)
        assert untrusted.post("/api/knowledge", json=body).status_code == 403
