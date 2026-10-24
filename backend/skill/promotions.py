"""只计算 A 提供且已核实的整单优惠；未知叠加关系时最多用一条。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def eligible_promotions(promotions: Sequence[Any], store_code: str) -> list[dict]:
    """接受 verified、eligible、store_codes、item_codes 等已核实事实。"""
    eligible: list[dict] = []
    for promotion in promotions:
        verified = _get(promotion, "verified") is True
        qualified = _get(promotion, "eligible") is True
        discount = _get(promotion, "discount_fen")
        item_codes = _get(promotion, "item_codes", _get(promotion, "item_ids", []))
        stores = _get(promotion, "store_codes", _get(promotion, "store_ids", []))
        minimum = _get(promotion, "min_quantity", 1)
        max_uses = _get(promotion, "max_uses", 1)
        if not verified or not qualified:
            continue
        if type(discount) is not int or discount <= 0:
            continue
        if not isinstance(_get(promotion, "id"), str) or not _get(promotion, "id") or not isinstance(_get(promotion, "description"), str) or not _get(promotion, "description"):
            continue
        if not isinstance(item_codes, (list, tuple)) or not item_codes or not all(isinstance(code, str) and code for code in item_codes):
            continue
        if not isinstance(stores, (list, tuple)) or (stores and store_code not in stores):
            continue
        if type(minimum) is not int or minimum < 1 or type(max_uses) is not int or max_uses < 1:
            continue
        eligible.append({
            "id": str(_get(promotion, "id", "")),
            "description": str(_get(promotion, "description", "")),
            "discount_fen": discount,
            "item_codes": tuple(item_codes),
            "min_quantity": minimum,
        })
    return eligible


def maximum_discount(promotions: Sequence[Any], store_code: str) -> int:
    """搜索剪枝上界；只可高估，不可低估实际可用优惠。"""
    return max((promo["discount_fen"] for promo in eligible_promotions(promotions, store_code)), default=0)


def apply_promotions(
    quantities: Mapping[str, int], menu_total_fen: int, promotions: Sequence[Any], store_code: str
) -> tuple[int, list[dict]]:
    if type(menu_total_fen) is not int or menu_total_fen < 0:
        raise ValueError("菜单金额必须是非负整数分")
    candidates = [
        promo for promo in eligible_promotions(promotions, store_code)
        if sum(quantities.get(code, 0) for code in promo["item_codes"]) >= promo["min_quantity"]
    ]
    if not candidates:
        return menu_total_fen, []
    best = max(candidates, key=lambda promo: (min(promo["discount_fen"], menu_total_fen), promo["id"]))
    saving = min(best["discount_fen"], menu_total_fen)
    return menu_total_fen - saving, [{"id": best["id"], "description": best["description"], "discount_fen": saving}]
