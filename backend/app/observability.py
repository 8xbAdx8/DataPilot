"""Bounded JSON events. Never accept arbitrary payloads, paths or exception messages."""

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from starlette.responses import JSONResponse

logger = logging.getLogger("datapilot")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
# Uvicorn's default access line includes raw query strings. Use http_request events instead.
logging.getLogger("uvicorn.access").disabled = True

FIELDS = {
    "request_id",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "response_bytes",
    "user_id",
    "analysis_id",
    "status",
    "error_code",
    "timings_ms",
    "usage",
}


def emit(event: str, **values):
    record = {"timestamp": datetime.now(UTC).isoformat(), "event": event}
    record.update({key: value for key, value in values.items() if key in FIELDS})
    logger.info(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


@contextmanager
def timed(metrics: dict, stage: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics[stage] = max(0, int((time.perf_counter() - started) * 1000))


class RequestTelemetryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        state = scope.setdefault("state", {})
        state["request_id"] = str(uuid.uuid4())  # Never echo an untrusted incoming identifier.
        started, status, size, sent = time.perf_counter(), 500, 0, False

        async def tracked_send(message):
            nonlocal status, size, sent
            if message["type"] == "http.response.start":
                status, sent = message["status"], True
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", state["request_id"].encode())]
                if scope["path"].startswith("/api/"):
                    message["headers"] = [
                        (key, value)
                        for key, value in message["headers"]
                        if key.lower() != b"cache-control"
                    ] + [(b"cache-control", b"no-store")]
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception:
            state["error_code"] = "internal_error"
            status = 500
            if sent:
                raise
            await JSONResponse(
                {
                    "detail": {
                        "code": "internal_error",
                        "message": "服务内部错误，请凭请求 ID 联系管理员。",
                    }
                },
                status_code=500,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, tracked_send)
        finally:
            route = scope.get("route")
            emit(
                "http_request",
                request_id=state["request_id"],
                user_id=state.get("user_id"),
                method=scope["method"]
                if scope["method"] in {"GET", "POST", "DELETE", "HEAD", "OPTIONS", "PUT", "PATCH"}
                else "OTHER",
                route=getattr(route, "path", "<unmatched>"),
                status_code=status,
                error_code=state.get("error_code"),
                duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
                response_bytes=size,
            )
