"""MCP 原始数据 → 公共模型的统一映射（A 职责）。

关键规则（均来自 2026-10-24 实测，详见 docs/mcp能力核实报告.md）：
- query-meals 返回 categories（code+tags）与 meals（详情字典），需二次 join
- currentPrice 不一定是可直接购买价：discountType 含「随单购」的商品必须
  同时购买麦金卡才能享受低价，否则按 originalPrice 结算
- 金额是「元」字符串，统一换算成整数分
- 菜单不含辣度字段，辣度来自 backend/data/spicy_rules.json 本地规则，
  未命中一律标 unknown，绝不默认当成不辣
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from ..schemas import MenuItemOut, SpicyLevel, StoreOut
from .mcp_provider import parse_price_fen

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_RULES: Optional[dict] = None


def _load_rules() -> dict:
    global _RULES
    if _RULES is None:
        path = _DATA_DIR / "spicy_rules.json"
        _RULES = json.loads(path.read_text(encoding="utf-8"))
    return _RULES


def classify_spicy(name: str) -> SpicyLevel:
    """按本地规则判定辣度；返回 unknown 时调用方必须如实透出。"""
    rules = _load_rules()
    if name in rules.get("spicy_exact", []):
        return "spicy"
    if name in rules.get("mild_exact", []):
        return "mild"
    for kw in rules.get("spicy_keywords", []):
        if kw in name:
            return "spicy"
    for kw in rules.get("mild_keywords", []):
        if kw in name:
            return "mild"
    return "unknown"


_SERVING_PATTERN = re.compile(r"(二人|三人|四人|五人|六人|双人|多人)")


def detect_servings(name: str) -> int:
    """从名称估计份数：默认 1，「双人餐 / 多人餐」等字样按人数放大。"""
    text = name.replace("分享餐", "双人餐")
    match = _SERVING_PATTERN.search(text)
    if not match:
        return 1
    token = match.group(1)
    table = {"二人": 2, "三人": 3, "四人": 4, "五人": 5, "六人": 6, "双人": 2}
    return table.get(token, 2 if token == "多人" else 1)


def map_stores(raw_stores: list[Any]) -> list[StoreOut]:
    stores: list[StoreOut] = []
    for raw in raw_stores or []:
        if not isinstance(raw, dict):
            continue
        start = raw.get("businessStartTime")
        end = raw.get("businessEndTime")
        stores.append(
            StoreOut(
                store_code=str(raw.get("storeCode", "")),
                name=raw.get("storeName", ""),
                address=raw.get("address", ""),
                distance=raw.get("distance"),
                business_status=raw.get("businessStatus"),
                business_hours=(f"{start}-{end}" if start and end else None),
                supports_reservation=bool(raw.get("reservation")),
            )
        )
    return stores


def map_menu(store_code: str, raw_menu: Any) -> tuple[list[MenuItemOut], list[str]]:
    """把 query-meals 的 data 转成平铺的商品列表（保留分类名）。"""
    rules = _load_rules()
    combo_keywords = rules.get("combo_keywords", [])
    categories: list[dict] = (raw_menu or {}).get("categories") or []
    meals: dict = (raw_menu or {}).get("meals") or {}

    items: list[MenuItemOut] = []
    seen: set[str] = set()
    category_names: list[str] = []

    for category in categories:
        category_name = (category.get("name") or "").replace("\n", "")
        if category_name not in category_names:
            category_names.append(category_name)
        for meal_ref in category.get("meals") or []:
            code = str(meal_ref.get("code", ""))
            if not code or code in seen:
                continue
            seen.add(code)
            detail = meals.get(code) or {}
            name = str(detail.get("name") or code)
            price_fen = parse_price_fen(detail.get("currentPrice")) or 0
            original_fen = parse_price_fen(detail.get("originalPrice")) or price_fen

            discount_type = detail.get("discountType")
            # 「随单购」类优惠必须同单买卡才成立，按原价口径处理并显式标注
            requires_membership = bool(discount_type) and "随单购" in str(discount_type)
            membership_note = None
            if requires_membership:
                membership_note = (
                    f"低价 {detail.get('currentPrice')} 元需随单购买麦金卡，"
                    f"未开卡按原价 {detail.get('originalPrice')} 元结算"
                )

            is_combo = any(kw in name for kw in combo_keywords)
            items.append(
                MenuItemOut(
                    code=code,
                    name=name,
                    price_fen=price_fen,
                    original_price_fen=original_fen,
                    category=category_name,
                    tags=list(meal_ref.get("tags") or []),
                    image=detail.get("image"),
                    is_combo=is_combo,
                    servings=detect_servings(name),
                    spicy_level=classify_spicy(name),
                    requires_membership=requires_membership,
                    membership_note=membership_note,
                )
            )
    return items, category_names
