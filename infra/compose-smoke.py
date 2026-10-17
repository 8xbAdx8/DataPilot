"""Verify an already running Compose stack using a disposable synthetic dataset."""

import json
import urllib.request
from urllib.error import HTTPError

BASE = "http://127.0.0.1:3000/api"


def request(path, data=None, headers=None, method=None):
    req = urllib.request.Request(
        BASE + path, data=data, headers=headers or {}, method=method
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        content = response.read()
        return json.loads(content) if content else None


def main():
    assert request("/health/ready")["status"] == "ok"
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
    finally:
        request(f"/datasets/{dataset['id']}", method="DELETE")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(f"Compose API smoke failed: HTTP {error.code}") from error
