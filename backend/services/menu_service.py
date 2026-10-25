# 整合说明：保留 A 的缓存与快照，提供 B 使用的服务端规划上下文。
"""菜单与门店数据服务（A 职责）。

运行模式（见 config.Settings.data_mode）：
- live  只走真实 MCP，失败直接抛错（用于证明真实集成）
- demo  只走本地快照，全程标注演示数据
- auto  优先真实 MCP，失败降级到快照并在响应里说明降级原因

live 拉取成功会自动把原始数据写入快照文件：
既作为演示兜底，也作为「真实调用证据」（含采集时间与 traceId）。
"""

from __future__ import annotations

import time
from typing import Optional

from ..providers import (
    McpError,
    McpMenuProvider,
    McpTimeout,
    MenuSnapshot,
    ProviderResult,
    SnapshotMenuProvider,
    save_snapshot,
)
from ..providers.snapshot_provider import load_snapshot
from ..providers.mapper import map_menu, map_stores
from ..schemas import MenuOut, PlanningContext, StoreListOut
from ..config import settings

_CACHE_TTL_SECONDS = 300.0


class _CacheEntry:
    def __init__(self, value: object, ttl: float = _CACHE_TTL_SECONDS) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl

    def valid(self) -> bool:
        return time.monotonic() < self.expires_at


class MenuService:
    def __init__(self) -> None:
        self._mcp = McpMenuProvider()
        self._snapshot = SnapshotMenuProvider()
        self._menu_cache: dict[str, _CacheEntry] = {}
        # 门店缓存按「运行模式 + 城市 + 关键词」分开，
        # 否则第二次换个城市搜索会直接命中第一次的结果
        self._stores_cache: dict[str, _CacheEntry] = {}

    # ------------------------------------------------------------ 内部

    async def _fetch_stores_raw(self, city: str, keyword: str) -> ProviderResult:
        return await self._mcp.get_stores(city, keyword)

    async def _fetch_menu_raw(self, store_code: str) -> ProviderResult:
        return await self._mcp.get_menu(store_code)

    async def _fetch_nutrition_raw(self) -> ProviderResult:
        return await self._mcp.get_nutrition()

    async def _collect_snapshot(self, store_code: str, menu_result: ProviderResult) -> None:
        """只保存已取得的菜单，保留旧证据，不为快照额外请求默认城市。"""
        try:
            snapshot = load_snapshot(store_code) or MenuSnapshot(store_code=store_code)
            snapshot.fetched_at = menu_result.fetched_at
            snapshot.data_source = menu_result.data_source
            snapshot.menu = menu_result.data
            snapshot.trace_ids["query-meals"] = menu_result.trace_id or ""
            save_snapshot(snapshot)
        except (McpError, McpTimeout, OSError, ValueError):
            # 快照刷新失败只记录，不向上抛：绝不能因为写盘失败
            # 把本次已经成功的在线结果变成错误响应
            pass

    # ------------------------------------------------------------ 对外

    async def get_stores(
        self, city: Optional[str] = None, keyword: Optional[str] = None
    ) -> StoreListOut:
        city = city or settings.default_city
        keyword = keyword or settings.default_store_keyword
        mode = settings.data_mode

        cache_key = f"{mode}:{city}:{keyword}"
        cached_entry = self._stores_cache.get(cache_key)
        if cached_entry and cached_entry.valid():
            cached: StoreListOut = cached_entry.value  # type: ignore[assignment]
            return cached

        result: Optional[ProviderResult] = None
        degraded: Optional[str] = None

        if mode in ("live", "auto"):
            try:
                result = await self._fetch_stores_raw(city, keyword)
            except McpTimeout as exc:
                if mode == "live":
                    raise
                degraded = f"真实 MCP 超时，已降级演示快照：{exc}"
            except McpError as exc:
                if mode == "live":
                    raise
                degraded = f"真实 MCP 不可用，已降级演示快照：{exc}"

        if result is None and mode in ("demo", "auto"):
            try:
                result = await self._snapshot.get_stores(city, keyword)
            except FileNotFoundError as exc:
                if mode == "demo":
                    raise McpError(f"演示快照缺失：{exc}") from exc
                raise McpError("真实 MCP 与本地快照均不可用") from exc

        stores = map_stores(result.data if result else [])
        out = StoreListOut(
            stores=stores,
            data_source=result.data_source if result else "demo_snapshot",
            fetched_at=result.fetched_at if result else "",
            degraded_reason=degraded,
        )
        self._stores_cache[cache_key] = _CacheEntry(out)
        return out

    async def get_menu(self, store_code: str) -> MenuOut:
        mode = settings.data_mode
        cache_key = f"{mode}:{store_code}"
        cached_entry = self._menu_cache.get(cache_key)
        if cached_entry and cached_entry.valid():
            cached: MenuOut = cached_entry.value  # type: ignore[assignment]
            return cached

        result: Optional[ProviderResult] = None
        degraded: Optional[str] = None

        if mode in ("live", "auto"):
            try:
                result = await self._fetch_menu_raw(store_code)
                # live 成功：刷新快照作为演示兜底与调用证据。
                # 快照写入本身失败不影响本次在线结果（已在 _collect_snapshot 内部兜住）
                await self._collect_snapshot(store_code, result)
            except McpTimeout as exc:
                if mode == "live":
                    raise
                degraded = f"真实 MCP 超时，已降级演示快照：{exc}"
            except McpError as exc:
                if mode == "live":
                    raise
                degraded = f"真实 MCP 不可用，已降级演示快照：{exc}"

        if result is None and mode in ("demo", "auto"):
            try:
                result = await self._snapshot.get_menu(store_code)
            except FileNotFoundError as exc:
                if mode == "demo":
                    raise McpError(f"演示快照缺失：{exc}") from exc
                raise McpError("真实 MCP 与本地快照均不可用") from exc

        items, categories = map_menu(store_code, result.data if result else None)
        out = MenuOut(
            store_code=store_code,
            items=items,
            categories=categories,
            data_source=result.data_source if result else "demo_snapshot",
            fetched_at=result.fetched_at if result else "",
            degraded_reason=degraded,
        )
        self._menu_cache[cache_key] = _CacheEntry(out)
        return out

    async def get_planning_context(self, store_code: str, menu: Optional[MenuOut] = None) -> PlanningContext:
        """只提供服务端事实；套餐由方案服务注入，缺少的会员/活动保持空值。"""
        menu = menu or await self.get_menu(store_code)
        return PlanningContext(
            store_code=store_code, data_source=menu.data_source,
            fetched_at=menu.fetched_at,
            price_basis="demo_snapshot" if menu.data_source == "demo_snapshot" else "menu_estimate",
        )

    async def aclose(self) -> None:
        await self._mcp.aclose()


menu_service = MenuService()
