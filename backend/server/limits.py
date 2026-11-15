from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    """Bound actual received bytes, including requests without Content-Length."""

    def __init__(self, app, maximum=2_097_152):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        messages = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.maximum or len(messages) >= 1024:
                return await JSONResponse({"detail": "Payload too large"}, status_code=413)(
                    scope, receive, send
                )
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay():
            return messages.pop(0) if messages else await receive()

        await self.app(scope, replay, send)
