"""Official checkout tools. Mutating calls are deliberately never retried here."""
from __future__ import annotations

from .mcp_provider import McpMenuProvider

# Exact production hosts verified from the official scanToPay documentation.
# Do not broaden to arbitrary *.mcd.cn, a returned URL, or a model-supplied host.
OFFICIAL_PAYMENT_HOSTS = frozenset({"m.mcd.cn", "mcp.mcd.cn", "mcd.cn"})


class CheckoutProvider:
    def __init__(self, client=None):
        self._client = client
        self._menu = McpMenuProvider() if client is None else None

    @property
    def client(self):
        return self._client if self._client is not None else self._menu.client

    async def calculate_price(self, arguments: dict) -> dict:
        return await self.client.call_tool("calculate-price", arguments)

    async def create_order(self, arguments: dict) -> dict:
        return await self.client.call_tool("create-order", arguments)

    async def query_order(self, order_id: str) -> dict:
        return await self.client.call_tool("query-order", {"orderId": order_id})

    async def aclose(self) -> None:
        if self._menu is not None:
            await self._menu.aclose()
        elif self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()
