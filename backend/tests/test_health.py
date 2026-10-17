from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_session
from app.main import app


def test_liveness_does_not_require_database():
    response = TestClient(app).get("/api/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_returns_503_when_database_is_unavailable():
    class UnavailableSession:
        def execute(self, _):
            raise SQLAlchemyError("internal connection information must not leak")

    app.dependency_overrides[get_session] = lambda: UnavailableSession()
    try:
        response = TestClient(app).get("/api/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "database": "unavailable"}
    finally:
        app.dependency_overrides.clear()
