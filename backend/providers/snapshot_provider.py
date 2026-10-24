"""快照演示数据提供方（A 职责）。

快照来自真实 MCP 采集（由 services/menu_service 在 live 拉取成功后自动落盘，
或用 backend/scripts/build_snapshot.py 手动刷新），按门店隔离并记录采集时间与 traceId。

演示模式返回的数据会整体标注 data_source=demo_snapshot，
前端必须把「演示数据」标识透出给用户。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..config import settings
from .base import MenuSnapshot, ProviderResult

_SNAPSHOT_DIR = settings.snapshot_path.parent


def snapshot_path_for(store_code: str) -> Path:
    return _SNAPSHOT_DIR / f"snapshot_{store_code}.json"


def load_snapshot(store_code: str) -> Optional[MenuSnapshot]:
    path = snapshot_path_for(store_code)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return MenuSnapshot(
        store_code=raw.get("store_code", store_code),
        fetched_at=raw.get("fetched_at", ""),
        stores=raw.get("stores"),
        menu=raw.get("menu"),
        nutrition=raw.get("nutrition"),
        trace_ids=raw.get("trace_ids") or {},
        data_source=raw.get("source", "live_mcp"),
    )


def save_snapshot(snapshot: MenuSnapshot) -> Path:
    """落盘快照。同时是「真实调用证据」的一部分（含采集时间与 traceId）。"""
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = snapshot_path_for(snapshot.store_code)
    payload = {
        "store_code": snapshot.store_code,
        "fetched_at": snapshot.fetched_at,
        "source": "live_mcp",
        "trace_ids": snapshot.trace_ids,
        "stores": snapshot.stores,
        "menu": snapshot.menu,
        "nutrition": snapshot.nutrition,
        "note": "本快照由真实麦当劳 MCP 采集生成，仅用于演示兜底，不修改原始数据",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class SnapshotMenuProvider:
    """从本地快照读取数据，结构与 MCP 在线返回一致。"""

    async def get_stores(self, city: str, keyword: str) -> ProviderResult:
        snapshot = load_snapshot(settings.default_store_code)
        if snapshot is None or snapshot.stores is None:
            raise FileNotFoundError("本地无可用门店快照")
        return ProviderResult(
            data=snapshot.stores,
            data_source="demo_snapshot",
            fetched_at=snapshot.fetched_at,
            trace_id=snapshot.trace_ids.get("query-nearby-stores"),
            tool_name="query-nearby-stores",
        )

    async def get_menu(self, store_code: str) -> ProviderResult:
        snapshot = load_snapshot(store_code)
        if snapshot is None or snapshot.menu is None:
            raise FileNotFoundError(f"门店 {store_code} 无本地菜单快照")
        return ProviderResult(
            data=snapshot.menu,
            data_source="demo_snapshot",
            fetched_at=snapshot.fetched_at,
            trace_id=snapshot.trace_ids.get("query-meals"),
            tool_name="query-meals",
        )

    async def get_nutrition(self) -> ProviderResult:
        snapshot = load_snapshot(settings.default_store_code)
        if snapshot is None or snapshot.nutrition is None:
            raise FileNotFoundError("本地无营养数据快照")
        return ProviderResult(
            data=snapshot.nutrition,
            data_source="demo_snapshot",
            fetched_at=snapshot.fetched_at,
            trace_id=snapshot.trace_ids.get("list-nutrition-foods"),
            tool_name="list-nutrition-foods",
        )
