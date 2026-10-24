"""套餐选配结构服务（A 职责：取数、缓存与降级说明，不做搭配决策）。

数据来源是 MCP 的 query-meal-detail（入参实测为 code，返回 rounds/choices）。
本服务只回答三件事：
1. 这份套餐实际由哪些轮次组成、每轮必选还是可选
2. 每轮有哪些选项、差价多少（解析失败记 None，不记 0）
3. 它到底含不含饮料、饮料能不能去掉

拿不到结构时如实返回「未知」，绝不写「不含饮料」。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from ..config import settings
from ..providers import (
    McpError,
    McpMenuProvider,
    McpTimeout,
    SnapshotMenuProvider,
)
from ..providers.mapper import map_meal_detail
from ..schemas import ComboDetailOut, DataSource

_CACHE_TTL_SECONDS = 600.0
# 单次请求最多取多少份套餐结构：套餐详情是逐份查询的，必须限流。
# 实测 1400011 有 44 份套餐，上限取 48 以覆盖整店，超出的部分如实记为「未取到」
MAX_COMBOS_PER_REQUEST = 48
# 并发上限，避免把上游打爆
MAX_CONCURRENCY = 4


@dataclass
class ComboDetailsResult:
    details: dict[str, ComboDetailOut] = field(default_factory=dict)
    data_source: DataSource = "demo_snapshot"
    fetched_at: str = ""
    # 拿不到结构的商品 code
    missing_codes: list[str] = field(default_factory=list)
    # 整体降级说明；为空表示结构完整
    degraded_reason: Optional[str] = None


class _CacheEntry:
    def __init__(self, value: ComboDetailOut) -> None:
        self.value = value
        self.expires_at = time.monotonic() + _CACHE_TTL_SECONDS

    def valid(self) -> bool:
        return time.monotonic() < self.expires_at


class ComboService:
    def __init__(self, mcp=None, snapshot=None) -> None:
        # 依赖可注入，便于测试与复用已有连接
        self._mcp = mcp or McpMenuProvider()
        self._snapshot = snapshot or SnapshotMenuProvider()
        self._cache: dict[str, _CacheEntry] = {}
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    # ------------------------------------------------------------ 内部

    def _cache_key(self, store_code: str, code: str) -> str:
        return f"{settings.data_mode}:{store_code}:{code}"

    async def _fetch_one(
        self, store_code: str, code: str
    ) -> tuple[Optional[ComboDetailOut], Optional[str]]:
        """返回 (套餐结构, 失败原因)。失败时不抛异常，套餐结构属于增强信息。"""
        mode = settings.data_mode
        degraded: Optional[str] = None
        raw: Optional[object] = None
        data_source: DataSource = "demo_snapshot"
        fetched_at = ""

        if mode in ("live", "auto"):
            try:
                async with self._semaphore:
                    result = await self._mcp.get_meal_detail(store_code, code)
                raw, data_source, fetched_at = result.data, result.data_source, result.fetched_at
            except (McpTimeout, McpError) as exc:
                # 套餐结构取不到不阻塞点餐主流程，但必须记清原因：
                # 没有结构就无法判断含不含饮料，相关硬约束只能判为不支持
                raw = None
                degraded = f"套餐结构获取失败（{type(exc).__name__}）：{exc}"

        if raw is None and mode in ("demo", "auto"):
            try:
                result = await self._snapshot.get_meal_detail(store_code, code)
                raw, data_source, fetched_at = result.data, result.data_source, result.fetched_at
                degraded = None
            except FileNotFoundError as exc:
                if degraded is None:
                    degraded = f"演示快照缺少该套餐结构：{exc}"

        if raw is None:
            return None, degraded or "未取到套餐结构"

        detail = map_meal_detail(raw, data_source, fetched_at, degraded_reason=None)
        if not detail.rounds:
            return None, "上游返回中缺少轮次信息，无法判断组成与是否含饮料"
        return detail, None

    # ------------------------------------------------------------ 对外

    async def get_details(
        self, store_code: str, codes: list[str]
    ) -> ComboDetailsResult:
        """批量取套餐结构。超出上限的部分记为缺失，不静默略过。"""
        unique_codes = [str(c) for c in dict.fromkeys(codes) if c]
        truncated: list[str] = []
        if len(unique_codes) > MAX_COMBOS_PER_REQUEST:
            truncated = unique_codes[MAX_COMBOS_PER_REQUEST:]
            unique_codes = unique_codes[:MAX_COMBOS_PER_REQUEST]

        result = ComboDetailsResult()
        pending: list[str] = []

        for code in unique_codes:
            entry = self._cache.get(self._cache_key(store_code, code))
            if entry and entry.valid():
                result.details[code] = entry.value
                if entry.value.fetched_at:
                    result.fetched_at = result.fetched_at or entry.value.fetched_at
                result.data_source = entry.value.source
            else:
                pending.append(code)

        if pending:
            fetched = await asyncio.gather(
                *(self._fetch_one(store_code, code) for code in pending)
            )
            for code, (detail, reason) in zip(pending, fetched):
                if detail is None:
                    result.missing_codes.append(code)
                    if reason and not result.degraded_reason:
                        result.degraded_reason = reason
                    continue
                self._cache[self._cache_key(store_code, code)] = _CacheEntry(detail)
                result.details[code] = detail
                result.data_source = detail.source
                result.fetched_at = result.fetched_at or detail.fetched_at

        result.missing_codes.extend(truncated)
        if truncated and not result.degraded_reason:
            result.degraded_reason = (
                f"候选套餐超过单次上限 {MAX_COMBOS_PER_REQUEST} 份，"
                f"其余 {len(truncated)} 份未取结构"
            )
        return result

    async def aclose(self) -> None:
        await self._mcp.aclose()


combo_service = ComboService()
