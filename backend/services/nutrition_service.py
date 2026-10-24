"""营养数据服务（A 职责：数据获取与解析，不含搭配逻辑）。

list-nutrition-foods 返回的是一段 CSV 风格文本（表头在花括号里声明字段），
只有商品名称没有商品 code，只能按名称匹配；缺失商品由 B 的方案模块标「未知」。
"""

from __future__ import annotations

import time
from typing import Optional

from ..providers import McpError, SnapshotMenuProvider
from ..config import settings
from ..schemas import NutritionOut
from .menu_service import menu_service

_CACHE_TTL_SECONDS = 3600.0

_FIELDS = [
    "productName",
    "nutritionDescription",
    "energyKj",
    "energyKcal",
    "protein",
    "fat",
    "carbohydrate",
    "sodium",
    "calcium",
]


def parse_nutrition_rows(raw: object) -> dict[str, dict]:
    """把 MCP 返回的营养文本解析成 {商品名: 字段字典}。"""
    if raw is None:
        return {}
    if isinstance(raw, list):  # 容错：如果未来返回改成数组
        rows = []
        for item in raw:
            if isinstance(item, dict):
                rows.append([str(item.get(f, "")) for f in _FIELDS])
            elif isinstance(item, (list, tuple)):
                rows.append([str(v) for v in item])
        parsed: dict[str, dict] = {}
        for row in rows:
            if len(row) < 2:
                continue
            parsed[row[0]] = dict(zip(_FIELDS, row))
        return parsed

    text = str(raw)
    parsed = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "{" in line or line.startswith("["):
            continue
        parts = line.split(",")
        if len(parts) < 5:
            continue
        # 名称里可能自带逗号（如 “苹果片”不含，但保险起见只把前段当名称）
        # 按表头固定 9 列解析：先从右往左取 8 个数值列，剩余全部归名称
        if len(parts) >= len(_FIELDS):
            tail = parts[-(len(_FIELDS) - 1):]
            name_parts = parts[: -(len(_FIELDS) - 1)]
        else:
            tail = parts[-(len(parts) - 1):]
            name_parts = parts[:1]
        row = [" ".join(p.strip() for p in name_parts if p.strip())] + [t.strip() for t in tail]
        if not row[0] or row[0].lower() == "null":
            continue
        parsed[row[0]] = dict(zip(_FIELDS, row))
    return parsed


def _to_float(value: object) -> Optional[float]:
    try:
        text = str(value).strip()
        if not text or text.lower() == "null":
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


class NutritionService:
    def __init__(self) -> None:
        self._cache: Optional[tuple[float, dict]] = None
        self._snapshot = SnapshotMenuProvider()

    async def get_nutrition_map(self) -> dict[str, NutritionOut]:
        """返回 {商品名: 营养信息}，供 B 的方案模块按名称匹配。"""
        if self._cache and time.monotonic() < self._cache[0]:
            return self._cache[1]

        raw: Optional[object] = None
        if settings.data_mode in ("live", "auto"):
            try:
                raw = (await menu_service._fetch_nutrition_raw()).data  # noqa: SLF001
            except McpError:
                if settings.data_mode == "live":
                    raise
                raw = None
        if raw is None:
            try:
                raw = (await self._snapshot.get_nutrition()).data
            except FileNotFoundError:
                raw = None

        rows = parse_nutrition_rows(raw)
        result: dict[str, NutritionOut] = {}
        for name, row in rows.items():
            result[name] = NutritionOut(
                energy_kcal=_to_float(row.get("energyKcal")),
                protein_g=_to_float(row.get("protein")),
                fat_g=_to_float(row.get("fat")),
                carbohydrate_g=_to_float(row.get("carbohydrate")),
                sodium_mg=_to_float(row.get("sodium")),
                matched=True,
            )
        self._cache = (time.monotonic() + _CACHE_TTL_SECONDS, result)
        return result


nutrition_service = NutritionService()
