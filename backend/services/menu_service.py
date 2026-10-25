"""Online menu/store cache. Offline fixtures belong to injected test providers only."""
from __future__ import annotations

import time
from typing import Optional

from ..config import settings
from ..providers import McpMenuProvider, McpError
from ..providers.mapper import map_menu, map_stores
from ..schemas import MenuOut, PlanningContext, StoreListOut

_CACHE_TTL_SECONDS = 300.0


class _CacheEntry:
    def __init__(self, value, ttl=_CACHE_TTL_SECONDS):
        self.value, self.expires_at = value, time.monotonic() + ttl

    def valid(self):
        return time.monotonic() < self.expires_at


class MenuService:
    def __init__(self, mcp=None):
        self._mcp = mcp if mcp is not None else McpMenuProvider()
        self._menu_cache, self._stores_cache = {}, {}

    async def _fetch_stores_raw(self, city, keyword):
        return await self._mcp.get_stores(city, keyword)

    async def _fetch_menu_raw(self, store_code):
        return await self._mcp.get_menu(store_code)

    async def _fetch_nutrition_raw(self):
        return await self._mcp.get_nutrition()

    @staticmethod
    def _online(result):
        if result.data_source != "live_mcp":
            raise McpError("没有取得在线数据，请重试。")
        return result

    async def get_stores(self, city: Optional[str] = None, keyword: Optional[str] = None):
        city, keyword = city or settings.default_city, keyword or settings.default_store_keyword
        key = (city, keyword)
        entry = self._stores_cache.get(key)
        if entry and entry.valid():
            return entry.value
        result = self._online(await self._fetch_stores_raw(city, keyword))
        out = StoreListOut(stores=map_stores(result.data), data_source="live_mcp", fetched_at=result.fetched_at)
        self._stores_cache[key] = _CacheEntry(out)
        return out

    async def get_menu(self, store_code):
        entry = self._menu_cache.get(store_code)
        if entry and entry.valid():
            return entry.value
        result = self._online(await self._fetch_menu_raw(store_code))
        items, categories = map_menu(store_code, result.data)
        out = MenuOut(store_code=store_code, items=items, categories=categories,
                      data_source="live_mcp", fetched_at=result.fetched_at)
        self._menu_cache[store_code] = _CacheEntry(out)
        return out

    async def get_planning_context(self, store_code, menu=None):
        menu = menu or await self.get_menu(store_code)
        return PlanningContext(store_code=store_code, data_source=menu.data_source,
                               fetched_at=menu.fetched_at, price_basis="menu_estimate")

    async def aclose(self):
        await self._mcp.aclose()


menu_service = MenuService()
