"""Serialize API work with credential swaps so stale clients never survive save."""
import asyncio


class RuntimeGateMiddleware:
    def __init__(self, app):
        self.app = app
        self._gate = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/v1/"):
            async with self._gate:
                await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)
