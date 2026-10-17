from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


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
            if not started:
                await JSONResponse(
                    {"detail": {"code": "request_too_large", "message": "请求体超过大小限制。"}},
                    status_code=413,
                )(scope, receive, send)
