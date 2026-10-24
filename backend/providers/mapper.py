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


def classify_food(name: str, is_combo: bool = False) -> tuple[str, str]:
    """只识别名称明确的单品；套餐和任选商品必须另有组成事实。"""
    if is_combo or re.search(r"套餐|件套|[单双多人]餐|任选|随心|拼盘|全家桶|分享", name):
        return "combo", "unknown"
    beverage = bool(re.search(r"可乐|雪碧|怡泉|红茶|奶茶|奶绿|咖啡|拿铁|奶铁|美式|牛奶|燕麦奶|豆浆|果汁|苹果汁|雪冰|卡布奇诺|美汁源", name))
    main = bool(re.search(r"堡|汉堡|麦香鸡|麦香鱼|麦满分|巨无霸", name))
    side = bool(re.search(r"薯条|薯饼|玉米杯|苹果片|鸡翅|V翅|麦乐鸡|鸡排|脆汁鸡|肉骨鸡|鸡块", name))
    dessert = bool(re.search(r"麦旋风|新地|圆筒|冰淇淋|苹果派|菠萝派", name))
    # 同名中混合出现饮品和固体餐品时无法证明其组成。
    if beverage and (main or side or dessert):
        return "unknown", "unknown"
    if beverage:
        return "beverage", "contains"
    if main:
        return "main", "none"
    if side:
        return "side", "none"
    if dessert:
        return "dessert", "none"
    return "unknown", "unknown"


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
            parsed_price = parse_price_fen(detail.get("currentPrice"))
            # 解析失败的零仅为旧接口占位，price_verified=false 禁止用于规划。
            price_verified = parsed_price is not None and parsed_price >= 0
            price_fen = parsed_price if price_verified else 0
            original_fen = parse_price_fen(detail.get("originalPrice"))
            if original_fen is None or original_fen < price_fen:
                original_fen = price_fen

            discount_type = detail.get("discountType")
            # 「随单购/麦金卡/早餐卡」类优惠都需要会员资格才成立（实测快照只出现
            # 随单购麦金卡优惠，但 MCP 枚举还有 麦金卡优惠/早餐卡优惠/随单购早餐卡优惠），
            # 一律按原价口径处理并显式标注，防止低价被直接求和
            discount_text = str(discount_type) if discount_type else ""
            requires_membership = any(
                kw in discount_text for kw in ("随单购", "麦金卡", "早餐卡")
            )
            membership_note = None
            if requires_membership:
                membership_note = (
                    f"菜单低价附带条件：{discount_text}；资格和不开卡应付尚未核实"
                )

            is_combo = any(kw in name for kw in combo_keywords)
            food_role, beverage_status = classify_food(name, is_combo)
            is_combo = food_role == "combo"
            spicy_level = classify_spicy(name)
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
                    servings=1 if food_role == "main" else 0,
                    spicy_level=spicy_level,
                    requires_membership=requires_membership,
                    membership_note=membership_note,
                    food_role=food_role,
                    beverage_status=beverage_status,
                    servings_verified=food_role in ("main", "side", "beverage", "dessert"),
                    non_spicy_servings=int(food_role == "main" and spicy_level == "mild"),
                    side_servings=int(food_role == "side"),
                    price_verified=price_verified,
                    available=price_verified,
                )
            )
    return items, category_names
