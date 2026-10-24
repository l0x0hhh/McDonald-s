"""快照构建脚本（A 职责）。

用途：
1. 演示模式的本地数据来源；
2. 「真实调用证据」——快照含采集时间与 traceId，可直接作为脱敏后的调用记录。

用法（仓库根目录）：
    .venv/Scripts/python.exe -m backend.scripts.build_snapshot [--store 1400011]
    .venv/Scripts/python.exe -m backend.scripts.build_snapshot --with-details

--with-details 会额外逐份拉取套餐选配结构（query-meal-detail）。
演示模式要支持「不带饮料」，必须带这个参数：快照里没有套餐结构，
就无法证明某份套餐含不含饮料。套餐是逐份查询的，因此有并发与数量上限。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import settings  # noqa: E402
from backend.providers import MenuSnapshot, McpMenuProvider, save_snapshot  # noqa: E402
from backend.providers.mapper import map_menu  # noqa: E402
from backend.providers.snapshot_provider import load_snapshot  # noqa: E402

MAX_DETAILS = 48
CONCURRENCY = 4


async def _collect_meal_details(
    provider: McpMenuProvider, store_code: str, menu_raw: object
) -> tuple[dict, int]:
    """逐份采套餐结构，返回 {code: 原始返回} 与失败数。"""
    items, _categories = map_menu(store_code, menu_raw)
    combo_codes = [i.code for i in items if i.is_combo][:MAX_DETAILS]
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(code: str):
        async with semaphore:
            try:
                result = await provider.get_meal_detail(store_code, code)
                return code, result.data
            except Exception:  # noqa: BLE001 — 单份失败不影响其他
                return code, None

    collected: dict = {}
    failed = 0
    for code, raw in await asyncio.gather(*(one(c) for c in combo_codes)):
        if raw is None:
            failed += 1
            continue
        collected[code] = raw
    return collected, failed


async def build(store_code: str, with_details: bool = False) -> Path:
    provider = McpMenuProvider()
    try:
        menu = await provider.get_menu(store_code)
        stores = await provider.get_stores(
            settings.default_city, settings.default_store_keyword
        )
        try:
            nutrition = await provider.get_nutrition()
        except Exception:  # noqa: BLE001 — 营养失败不阻塞快照
            nutrition = None

        trace_ids = {
            "query-meals": menu.trace_id or "",
            "query-nearby-stores": stores.trace_id or "",
            **({"list-nutrition-foods": nutrition.trace_id or ""} if nutrition else {}),
        }

        meal_details: dict = {}
        if with_details:
            # 没有套餐结构就无法判断含不含饮料，「不带饮料」在演示模式下会不可用
            collected, failed = await _collect_meal_details(
                provider, store_code, menu.data
            )
            meal_details = collected
            trace_ids["query-meal-detail"] = f"{len(collected)} 份套餐结构"
            print(f"套餐结构：成功 {len(collected)} 份，失败 {failed} 份")
        else:
            previous = load_snapshot(store_code)
            if previous is not None and previous.meal_details:
                # 不带 --with-details 时保留已有的套餐结构，不被本次刷新清掉
                meal_details = previous.meal_details

        snapshot = MenuSnapshot(
            store_code=store_code,
            fetched_at=menu.fetched_at,
            trace_ids=trace_ids,
        )
        snapshot.menu = menu.data
        snapshot.stores = stores.data
        snapshot.nutrition = nutrition.data if nutrition else None
        snapshot.meal_details = meal_details or None
        path = save_snapshot(snapshot)
        print(f"快照已写入: {path}")
        print(f"采集时间: {snapshot.fetched_at}")
        print(f"traceIds: {json.dumps(snapshot.trace_ids, ensure_ascii=False)}")
        return path
    finally:
        await provider.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="从真实 MCP 采集并落盘演示快照")
    parser.add_argument("--store", default=settings.default_store_code)
    parser.add_argument(
        "--with-details",
        action="store_true",
        help="同时采集套餐选配结构（演示模式支持「不带饮料」所需）",
    )
    args = parser.parse_args()
    asyncio.run(build(args.store, args.with_details))


if __name__ == "__main__":
    main()
