"""Verify an already running Compose stack using a disposable synthetic dataset."""

import http.cookiejar
import json
import os
import secrets
import subprocess
import urllib.request
from urllib.error import HTTPError

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:3000/api")
CLIENT = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
)


def request(path, data=None, headers=None, method=None):
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"X-DataPilot-CSRF": "1", **(headers or {})},
        method=method,
    )
    with CLIENT.open(req, timeout=60) as response:
        content = response.read()
        return json.loads(content) if content else None


def json_request(path, body):
    return request(
        path, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )


def expect_logged_out():
    try:
        request("/auth/me")
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Revoked session still authenticated")


def main():
    assert request("/health/ready")["status"] == "ok"
    config = request("/config")
    assert config["provider"] == "mock", (
        "Smoke requires explicit Mock; refusing paid model calls"
    )
    if os.environ.get("SMOKE_REQUIRE_RAG") == "1":
        assert config["embedding"]["provider"] == "fake", (
            "RAG acceptance requires explicit Fake vectors"
        )
    username, password = "smoke-" + secrets.token_hex(8), secrets.token_urlsafe(24)
    provision = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "backend",
            "python",
            "-m",
            "app.accounts",
            "create-user",
            "--username",
            username,
            "--password-stdin",
        ],
        input=password + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert provision.returncode == 0, "Smoke account provisioning failed"
    request(
        "/auth/login",
        json.dumps({"username": username, "password": password}).encode(),
        {"Content-Type": "application/json"},
    )
    boundary = "datapilot-smoke-boundary"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        'filename="compose-smoke.csv"\r\nContent-Type: text/csv\r\n\r\n'
        f"region,revenue\nEast,10\nWest,20\nEast,30\n\r\n--{boundary}--\r\n"
    ).encode()
    dataset = request(
        "/datasets", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    try:
        result = request(
            f"/datasets/{dataset['id']}/analyses",
            json.dumps({"question": "按 region 统计 revenue 总和"}).encode(),
            {"Content-Type": "application/json"},
        )
        assert result["status"] == "success"
        assert result["result"]["rows"] == [
            {"region": "East", "metric": 40},
            {"region": "West", "metric": 20},
        ]
        print("HTTP health + upload + Mock SQL + real DuckDB result: PASS")
        assert result["telemetry"]["workflow"]["engine"] == "langgraph"
        assert result["telemetry"]["workflow"]["agent"]["tool_calls"] >= 2
        if config["embedding"]["provider"] == "fake":
            saved = json_request(
                f"/datasets/{dataset['id']}/knowledge",
                {
                    "title": "销售额",
                    "source_type": "field_alias",
                    "content": '{"alias":"销售额","field":"revenue"}',
                },
            )
            assert saved["status"] == "ready" and saved["fake_embedding"]
            rag = json_request(
                f"/datasets/{dataset['id']}/analyses", {"question": "销售额总和"}
            )
            assert rag["result"]["rows"] == [{"metric": 60}]
            context = rag["telemetry"]["workflow"]["rag"]
            assert (
                context["used"]
                and context["citations"][0]["document_id"] == saved["id"]
            )
            request(f"/knowledge/{saved['id']}", method="DELETE")
            assert request(f"/datasets/{dataset['id']}/knowledge") == []
            print(
                "Compose pgvector + knowledge CRUD + RAG citation + actual SUM=60: PASS"
            )
    finally:
        request(f"/datasets/{dataset['id']}", method="DELETE")
        request("/auth/logout", data=b"", method="POST")
    replacement = secrets.token_urlsafe(24)
    try:
        json_request("/auth/login", {"username": username, "password": password})
        sessions = request("/auth/sessions")
        assert len(sessions) == 1 and sessions[0]["current"]
        assert set(sessions[0]) == {"id", "created_at", "expires_at", "current"}
        json_request(
            "/auth/password",
            {"current_password": password, "new_password": replacement},
        )
        expect_logged_out()
        try:
            json_request("/auth/login", {"username": username, "password": password})
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("Old password accepted after password change")
        json_request("/auth/login", {"username": username, "password": replacement})
        json_request("/auth/logout-all", {"current_password": replacement})
        expect_logged_out()
        print("HTTP password change + session inventory + revocation: PASS")
    finally:
        request("/auth/logout", data=b"", method="POST")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(f"Compose API smoke failed: HTTP {error.code}") from error
