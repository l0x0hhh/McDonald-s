"""Compare verified membership prices for one identical basket.

``facts`` is supplied by A; this module never obtains prices or account status.
It contains ``without_card``, ``existing_card`` and ``new_card`` quotes. Each
quote has ``basket`` (items with code, quantity and selected_options/spec),
``payable_fen``, ``verified=True`` and ``eligible=True``. The new-card quote
also needs an explicit ``includes_card_fee`` flag (or the equivalent top-level
``card_fee_included_in_new_quote``). The top level supplies ``card_fee_fen``
and ``card_fee_verified=True``. A quote may additionally carry price_basis,
source and fetched_at. Unknown facts produce unavailable, never a made-up
comparison based on a menu's original price.

A conditional habit estimate additionally requires explicit evidence that the
same basket and benefit repeat, the card covers the selected period and uses,
and mutual exclusions and other restrictions were checked. Frequency and
period come only from the user's stated requirements.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from math import ceil
from typing import Any


def _data(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    return {}


def _money(value: Any) -> bool:
    return type(value) is int and value >= 0


def _structure(value: Any) -> str | None:
    if not isinstance(value, (list, tuple)):
        return None
    try:
        # Option and component order is not part of the purchased specification.
        parts = sorted(json.dumps(_data(part) or part, ensure_ascii=False, sort_keys=True)
                       for part in value)
        return json.dumps(parts, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _basket_signature(value: Any) -> tuple[tuple[str, str, str, int], ...] | None:
    if isinstance(value, Mapping) and "items" in value:
        value = value["items"]
    elif hasattr(value, "items") and not isinstance(value, (list, tuple)):
        value = _data(value).get("items", value)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        return None
    counts: dict[tuple[str, str, str], int] = {}
    for raw in value:
        item = _data(raw)
        code = item.get("code") or item.get("id")
        quantity = item.get("quantity")
        if not isinstance(code, str) or not code or type(quantity) is not int or quantity < 1:
            return None
        options = item.get("selected_options")
        spec = item.get("spec")
        components = item.get("components")
        is_combo = item.get("is_combo") is True or item.get("food_role") == "combo"
        # Display text cannot prove that all three quotes selected the same
        # final specification. Fixed combos need verified component structure.
        if spec and not options and not components:
            return None
        if is_combo and not options and not components:
            return None
        option_key = _structure(options or [])
        component_key = _structure(components or [])
        if option_key is None or component_key is None:
            return None
        key = (code, option_key, component_key)
        counts[key] = counts.get(key, 0) + quantity
    return tuple(sorted((code, options, components, quantity)
                        for (code, options, components), quantity in counts.items()))


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def _unavailable(reason: str, *, same_basket: bool | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "same_basket": same_basket,
        "without_card_fen": None,
        "existing_card_food_fen": None,
        "new_card_food_fen": None,
        "new_card_total_fen": None,
        "card_fee_fen": None,
        "existing_card_saving_fen": None,
        "net_saving_fen": None,
        "price_basis": None,
        "source": None,
        "fetched_at": None,
        "assumptions": [],
        "habit_estimate": None,
    }


def _habit_estimate(
    facts: Mapping[str, Any], requirements: Any, without_card_fen: int,
    new_card_food_fen: int, card_fee_fen: int,
) -> dict[str, Any] | None:
    req = _data(requirements)
    frequency = req.get("meal_frequency_per_month")
    horizon = req.get("habit_horizon_months")
    if type(frequency) is not int or frequency < 0 or type(horizon) is not int or horizon < 1:
        return None
    uses = frequency * horizon
    validity = facts.get("card_validity_months")
    max_uses = facts.get("benefit_max_uses")
    if (
        facts.get("fixed_basket") is not True
        or facts.get("repeat_basket_eligible") is not True
        or facts.get("repeat_saving_verified") is not True
        or facts.get("mutual_exclusion_verified") is not True
        or facts.get("other_restrictions_verified") is not True
        or type(validity) is not int or validity < horizon
        or not (facts.get("benefit_unlimited") is True or
                (type(max_uses) is int and max_uses >= uses))
    ):
        return None
    per_visit = without_card_fen - new_card_food_fen
    break_even = 0 if card_fee_fen == 0 else ceil(card_fee_fen / per_visit) if per_visit > 0 else None
    return {
        "status": "available",
        "frequency_per_month": frequency,
        "horizon_months": horizon,
        "expected_uses": uses,
        "per_visit_saving_fen": per_visit,
        "break_even_uses": break_even,
        "period_net_saving_fen": uses * per_visit - card_fee_fen,
        "breaks_even_in_period": break_even is not None and uses >= break_even,
        "assumptions": ["用户自报频次", "固定篮子与已核实权益在分析周期内重复适用", "未来价格不保证不变"],
    }


def compare_membership(facts: Any, requirements: Any = None, basket: Any = None) -> dict[str, Any]:
    """Return the three same-basket scenarios or an explicit unavailable state.

    ``payable_fen`` on a new-card quote includes the fee exactly when its
    explicit ``includes_card_fee`` flag is true. An included fee is subtracted
    to derive repeatable food cost, then never charged a second time.
    """
    data = _data(facts)
    if not data:
        return _unavailable("缺少已核实的会员报价与卡费事实")
    quote_names = ("without_card", "existing_card", "new_card")
    quotes = {name: _data(data.get(name)) for name in quote_names}
    if any(not quote for quote in quotes.values()):
        return _unavailable("缺少不开卡、已有卡或新开卡的同篮子报价")
    expected = _basket_signature(basket) if basket is not None else _basket_signature(quotes["without_card"].get("basket"))
    signatures = {name: _basket_signature(quote.get("basket")) for name, quote in quotes.items()}
    if expected is None or any(signature is None for signature in signatures.values()):
        return _unavailable("商品、数量或规格未确认，无法核对同一篮子")
    if any(signature != expected for signature in signatures.values()):
        return _unavailable("三个会员情境不是同一商品、数量和规格", same_basket=False)
    for name, quote in quotes.items():
        if quote.get("verified") is not True or quote.get("eligible") is not True:
            return _unavailable(f"{name} 的报价或资格未核实", same_basket=True)
        if quote.get("eligibility_verified") is False or quote.get("price_verified") is False:
            return _unavailable(f"{name} 的报价或资格未核实", same_basket=True)
        basis = quote.get("price_basis", data.get("price_basis"))
        source = quote.get("source", data.get("source"))
        fetched = quote.get("fetched_at", data.get("fetched_at"))
        if basis not in ("live_quote", "demo_snapshot"):
            return _unavailable(f"{name} 缺少已核实的报价依据", same_basket=True)
        if not isinstance(source, str) or not source or _timestamp(fetched) is None:
            return _unavailable(f"{name} 缺少可追溯来源或报价时间", same_basket=True)
        valid_until = quote.get("valid_until", data.get("valid_until"))
        if valid_until is not None:
            end = _timestamp(valid_until)
            if end is None or end <= datetime.now(timezone.utc):
                return _unavailable(f"{name} 的报价已过期或有效期不明", same_basket=True)
        amount = quote.get("payable_fen", quote.get("food_payable_fen"))
        if not _money(amount):
            return _unavailable(f"{name} 缺少有效的应付金额", same_basket=True)
    fee = data.get("card_fee_fen")
    if data.get("card_fee_verified") is not True or not _money(fee):
        return _unavailable("卡费未核实", same_basket=True)
    bases = {quote.get("price_basis", data.get("price_basis")) for quote in quotes.values()}
    if len(bases) != 1:
        return _unavailable("三个情境的在线报价与演示快照不能混用", same_basket=True)
    new_quote = quotes["new_card"]
    included = new_quote.get("includes_card_fee", data.get("card_fee_included_in_new_quote"))
    top_included = data.get("card_fee_included_in_new_quote")
    if type(included) is not bool or (type(top_included) is bool and included != top_included):
        return _unavailable("新开卡报价是否已含卡费不明确", same_basket=True)
    if quotes["without_card"].get("includes_card_fee") is True or quotes["existing_card"].get("includes_card_fee") is True:
        return _unavailable("不开卡或已有卡报价错误地包含新增卡费", same_basket=True)
    without = quotes["without_card"]["payable_fen"] if "payable_fen" in quotes["without_card"] else quotes["without_card"]["food_payable_fen"]
    existing = quotes["existing_card"]["payable_fen"] if "payable_fen" in quotes["existing_card"] else quotes["existing_card"]["food_payable_fen"]
    new_quote_total = new_quote["payable_fen"] if "payable_fen" in new_quote else new_quote["food_payable_fen"]
    new_food = new_quote_total - fee if included else new_quote_total
    if new_food < 0:
        return _unavailable("新开卡报价低于已含卡费，数据相互矛盾", same_basket=True)
    new_total = new_quote_total if included else new_quote_total + fee
    sources = {name: quote.get("source") for name, quote in quotes.items()}
    result = {
        "status": "available",
        "reason": None,
        "same_basket": True,
        "without_card_fen": without,
        "existing_card_food_fen": existing,
        "new_card_food_fen": new_food,
        "new_card_total_fen": new_total,
        "card_fee_fen": fee,
        "existing_card_saving_fen": without - existing,
        "net_saving_fen": without - new_total,
        "price_basis": next(iter(bases)),
        "source": data.get("source") or sources,
        "fetched_at": data.get("fetched_at") or {name: quote.get("fetched_at") for name, quote in quotes.items()},
        "assumptions": ["三种情境使用同一商品、数量和规格", "资格按传入的已核实事实计算"],
        "habit_estimate": _habit_estimate(data, requirements, without, new_food, fee),
    }
    return result
