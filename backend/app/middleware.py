from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.config import get_settings


class SecurityMiddleware:
    """Custom header + explicit origin allowlist for cookie-authenticated mutations."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get("origin")
            if (
                headers.get("x-datapilot-csrf") != "1"
                or (origin is not None and origin not in get_settings().auth_trusted_origins)
                or headers.get("sec-fetch-site") == "cross-site"
            ):
                scope.setdefault("state", {})["error_code"] = "csrf_rejected"
                return await JSONResponse(
                    {"detail": {"code": "csrf_rejected", "message": "请求来源校验失败。"}},
                    status_code=403,
                )(scope, receive, send)

        async def no_store(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        return await self.app(scope, receive, no_store)


class RequestTooLarge(HTTPException):
    def __init__(self):
        super().__init__(
            413, detail={"code": "request_too_large", "message": "请求体超过大小限制。"}
        )


class BodyLimitMiddleware:
    """Count streamed bytes before multipart spooling can exhaust disk."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        total = 0
        started = False

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                raise RequestTooLarge
            return message

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracked_send)
        except RequestTooLarge:
            scope.setdefault("state", {})["error_code"] = "request_too_large"
            if not started:
                await JSONResponse(
                    {"detail": {"code": "request_too_large", "message": "请求体超过大小限制。"}},
                    status_code=413,
                )(scope, receive, send)
