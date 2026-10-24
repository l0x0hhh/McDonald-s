"""快照构建脚本（A 职责）。

用途：
1. 演示模式的本地数据来源；
2. 「真实调用证据」——快照含采集时间与 traceId，可直接作为脱敏后的调用记录。

用法（仓库根目录）：
    .venv/Scripts/python.exe -m backend.scripts.build_snapshot [--store 1400011]
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


async def build(store_code: str) -> Path:
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

        snapshot = MenuSnapshot(
            store_code=store_code,
            fetched_at=menu.fetched_at,
            trace_ids={
                "query-meals": menu.trace_id or "",
                "query-nearby-stores": stores.trace_id or "",
                **({"list-nutrition-foods": nutrition.trace_id or ""} if nutrition else {}),
            },
        )
        snapshot.menu = menu.data
        snapshot.stores = stores.data
        snapshot.nutrition = nutrition.data if nutrition else None
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
    args = parser.parse_args()
    asyncio.run(build(args.store))


if __name__ == "__main__":
    main()
