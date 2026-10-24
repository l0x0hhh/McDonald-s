# 修订说明：排除未知现价，核查混合轮次中的饮料，并将未选配餐份保留为估计。
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
import logging
import re
from pathlib import Path
from typing import Any, Optional

from ..schemas import (
    BeverageStatus,
    ComboDetailOut,
    DataSource,
    ComboOptionOut,
    ComboRoundOut,
    FoodRole,
    MenuItemOut,
    SpicyLevel,
    StoreOut,
)
from .mcp_provider import parse_diff_price_fen, parse_price_fen

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_RULES: Optional[dict] = None
_ROLE_RULES: Optional[dict] = None
# 只记录商品标识与失败原因，方便定位被排除的未知价格商品。
_LOGGER = logging.getLogger(__name__)


def _load_rules() -> dict:
    global _RULES
    if _RULES is None:
        path = _DATA_DIR / "spicy_rules.json"
        _RULES = json.loads(path.read_text(encoding="utf-8"))
    return _RULES


def _load_role_rules() -> dict:
    global _ROLE_RULES
    if _ROLE_RULES is None:
        path = _DATA_DIR / "food_roles.json"
        _ROLE_RULES = json.loads(path.read_text(encoding="utf-8"))
    return _ROLE_RULES


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


def is_beverage_round(round_name: str) -> bool:
    """判断一轮选配是不是「饮料轮」。

    实测套餐的饮料轮叫「选择套餐内饮料」，minQuantity=1 —— 也就是说
    **套餐里的饮料去不掉**，这是「不带饮料」必须走单品组合的根本原因。
    """
    keywords = _load_role_rules().get("beverage_round_keywords", [])
    return any(kw in round_name for kw in keywords)


def classify_food_role(
    name: str, category: str, is_combo: bool
) -> tuple[FoodRole, BeverageStatus, bool, str]:
    """判定餐品角色与是否含饮料。

    返回 (food_role, beverage_status, is_merchandise, evidence)。
    判定顺序：非餐品 → 套餐 → 小食 → 饮料 → 主食 → 分类 → unknown。
    名称优先于分类，因为「炸鸡」分类里同时有整块炸鸡和麦乐鸡/鸡翅这类小食。
    未命中一律返回 unknown，绝不能把「没判定出来」当成「不含饮料」。
    """
    rules = _load_role_rules()
    text = name or ""

    for kw in rules.get("skip_keywords", []):
        if kw in text:
            return "unknown", "unknown", True, f"命中非餐品关键词「{kw}」"

    if is_combo:
        # 套餐的实际角色由 query-meal-detail 的轮次决定，这里先按「套餐」占位
        return "combo", "unknown", False, "套餐：组成待 query-meal-detail 核实"

    for kw in rules.get("side_keywords", []):
        if kw in text:
            return "side", "none", False, f"名称命中小食关键词「{kw}」"

    for kw in rules.get("beverage_keywords", []):
        if kw in text:
            return "beverage", "contains", False, f"名称命中饮料关键词「{kw}」"

    for kw in rules.get("main_keywords", []):
        if kw in text:
            return "main", "none", False, f"名称命中主食关键词「{kw}」"

    for kw, role in rules.get("category_roles", {}).items():
        if kw in (category or ""):
            status: BeverageStatus = "contains" if role == "beverage" else "none"
            return role, status, False, f"分类「{category}」命中「{kw}」"

    return "unknown", "unknown", False, "未命中任何角色规则，角色与含饮料情况均未知"


def is_staple_main(name: str, category: str) -> bool:
    """是否是「能当一顿正餐」的主食（汉堡 / 卷 / 鸡排）。

    炸鸡块、鸡翅虽然也归为主食类，但让 6 个人各吃一块炸鸡并不构成一餐；
    「不带饮料」这类硬约束方案必须建立在正餐主食上。
    """
    rules = _load_role_rules()
    if any(kw in (category or "") for kw in rules.get("staple_categories", [])):
        return True
    return any(kw in (name or "") for kw in rules.get("staple_keywords", []))


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
            # 保留真实零元；未知或负价商品不进入可规划菜单，维持整数分接口兼容。
            price_fen = parse_price_fen(detail.get("currentPrice"))
            if price_fen is None or price_fen < 0:
                _LOGGER.warning("门店 %s 商品 %s 现价无效，已排除", store_code, code)
                continue
            original_fen = parse_price_fen(detail.get("originalPrice"))
            original_unknown = original_fen is None or original_fen < price_fen
            if original_unknown:
                original_fen = price_fen

            discount_type = detail.get("discountType")
            # 低价需要额外资格才成立的两类情况，一律按原价口径处理并显式标注：
            # 1) discountType 含「随单购/麦金卡/早餐卡」——必须先买卡才享受低价
            # 2) 名称含「学生专享」——需学生身份认证，MCP 未返回资格字段，无法核验
            # 混进最省方案会给出「6 人 60 元」这种到店付不出来的报价
            discount_text = str(discount_type) if discount_type else ""
            card_keywords = ("随单购", "麦金卡", "早餐卡")
            needs_card = any(kw in discount_text for kw in card_keywords)
            needs_student = "学生专享" in name

            requires_membership = needs_card or needs_student
            membership_note = None
            if needs_card:
                membership_note = (
                    f"低价 {detail.get('currentPrice')} 元需{discount_text}"
                    f"（随单购买对应卡），未满足资格按原价 "
                    f"{detail.get('originalPrice')} 元结算"
                )
            elif needs_student:
                membership_note = (
                    f"{detail.get('currentPrice')} 元为「学生专享」价，"
                    "需学生身份认证；MCP 未返回资格字段，本版无法核验，不按此价计入方案"
                )

            is_combo = any(kw in name for kw in combo_keywords)
            food_role, beverage_status, is_merch, evidence = classify_food_role(
                name, category_name, is_combo
            )
            # 缺少可用原价时按现价作参考，避免虚构折扣。
            if original_unknown:
                evidence += "；原价缺失或低于现价，原价参考按现价列示，不计优惠"
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
                    food_role=food_role,
                    beverage_status=beverage_status,
                    role_evidence=evidence,
                    is_merchandise=is_merch,
                )
            )
    return items, category_names


# ---------------------------------------------------------------- 套餐选配结构


def map_meal_detail(
    raw_payload: Any,
    data_source: "DataSource",
    fetched_at: str = "",
    degraded_reason: Optional[str] = None,
) -> ComboDetailOut:
    """把 query-meal-detail 的返回映射成套餐选配结构。

    实测返回：data = {code, name, rounds[], image, supportModify}
    rounds[] = {id, name, quantity, minQuantity, maxQuantity, choices[]}
    choices[] = {code, name, quantity, maxQuantity, isDefault, diffPrice, ...}

    diffPrice 形如 "+ ¥1"；解析失败记 None，绝不记 0（0 会被当成「确认不加价」）。
    """
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    code = str(data.get("code") or "")
    name = str(data.get("name") or code)

    rounds: list[ComboRoundOut] = []
    for raw_round in data.get("rounds") or []:
        if not isinstance(raw_round, dict):
            continue
        round_name = str(raw_round.get("name") or "")
        # 饮料轮里的所有选项都是饮料，不受名称关键词影响
        round_is_beverage = is_beverage_round(round_name)

        options: list[ComboOptionOut] = []
        for raw_choice in raw_round.get("choices") or []:
            if not isinstance(raw_choice, dict):
                continue
            option_name = str(raw_choice.get("name") or "")
            diff_raw = raw_choice.get("diffPrice")
            if round_is_beverage:
                role: FoodRole = "beverage"
                status: BeverageStatus = "contains"
            else:
                role, status, _merch, _evidence = classify_food_role(
                    option_name, "", False
                )
            options.append(
                ComboOptionOut(
                    code=str(raw_choice.get("code") or ""),
                    name=option_name,
                    diff_price_fen=parse_diff_price_fen(diff_raw),
                    diff_price_raw=(None if diff_raw is None else str(diff_raw)),
                    is_default=bool(raw_choice.get("isDefault")),
                    food_role=role,
                    beverage_status=status,
                    max_quantity=raw_choice.get("maxQuantity"),
                )
            )
        rounds.append(
            ComboRoundOut(
                round_id=str(raw_round.get("id") or ""),
                name=round_name,
                min_quantity=raw_round.get("minQuantity"),
                max_quantity=raw_round.get("maxQuantity"),
                # 默认数量取轮次 quantity；choice.quantity 在实测默认项中可能为 0。
                default_quantity=raw_round.get("quantity"),
                options=options,
                is_beverage_round=round_is_beverage,
            )
        )

    # 这是全部可选项的保守摘要；蓝区/粉区也要检查，实际方案另按已选项核验。
    beverage_status: BeverageStatus = "unknown"
    options = [option for round_ in rounds for option in round_.options]
    if any(option.beverage_status == "contains" for option in options):
        beverage_status = "contains"
    elif rounds and all(round_.options for round_ in rounds) and all(
        option.beverage_status == "none" for option in options
    ):
        beverage_status = "none"

    detail = ComboDetailOut(
        code=code,
        name=name,
        rounds=rounds,
        support_modify=bool(data.get("supportModify")),
        beverage_status=beverage_status,
        servings=detect_servings(name),
        # 有轮次不等于已知餐份：必须核实具体主食选项及数量后才能确认。
        servings_verified=False,
        source=data_source,
        fetched_at=fetched_at or str(payload.get("datetime") or ""),
        degraded_reason=degraded_reason,
    )
    detail.composition = describe_composition(detail)
    return detail


def describe_composition(detail: ComboDetailOut) -> Optional[str]:
    """把轮次总结成人能看懂的组成说明，如「1 主食 + 1 小食 + 1 饮料（必选）」。"""
    if not detail.rounds:
        return None
    parts: list[str] = []
    for r in detail.rounds:
        # 混合轮次展示全部角色，不能因存在主食选项就把整轮宣称为主食。
        labels = {"main": "主食", "side": "小食", "beverage": "饮料", "unknown": "未知餐品", "combo": "组合"}
        roles = list(dict.fromkeys(o.food_role for o in r.options))
        role_text = "/".join(labels[role] for role in roles) or r.name or "未知选项"
        count = str(r.min_quantity) if r.min_quantity is not None else "数量未知"
        suffix = "（必选）" if r.required else "（可选）"
        if r.min_quantity is None:
            suffix = "（是否必选未知）"
        parts.append(f"{count} {role_text}{suffix}")
    return " + ".join(parts)
