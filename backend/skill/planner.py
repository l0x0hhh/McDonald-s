# 整合说明：兼容 A 的结构化选配、无限轮次与规格原价，保留 B 的搜索排序。
"""基于 A 的门店事实生成有限候选，并复核金额与所有硬约束。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import ceil
from typing import Any

from ..schemas import (
    ConstraintCheck,
    MenuItemOut,
    NutritionOut,
    PlanItemOut,
    PlanOut,
    Requirements,
    SelectedOptionOut,
)
from .promotions import apply_promotions, maximum_discount


class MenuError(ValueError):
    """门店菜单格式错误，不能安全规划。"""


class PlanValidationError(ValueError):
    """方案与门店事实或需求不一致，不能保存/结算。"""


_TITLES = {"cheapest": "最省方案", "balanced": "搭配多样方案", "taste": "口味匹配方案"}
_TASTE_WORDS = {
    "beef": ("牛肉", "牛堡", "安格斯"),
    "chicken": ("鸡肉", "鸡腿", "鸡块", "麦乐鸡", "鸡翅", "鸡排"),
    "fish": ("鱼肉", "鳕鱼", "鱼排", "麦香鱼"),
    "fried": ("炸", "薯条", "鸡块", "麦乐鸡", "脆"),
    "grilled": ("板烧", "烤", "炙"),
    "sweet": ("甜", "派", "冰淇淋", "圣代"),
    "sugar_free": ("无糖", "零糖", "零度"),
}


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _context_parts(context: Any) -> tuple[str, list[dict], dict[str, dict], str]:
    store_code = _get(context, "store_code", "") or ""
    promotions = _get(context, "promotions", []) or []
    combo_details = _get(context, "combo_details", {}) or {}
    price_basis = _get(context, "price_basis", "menu_estimate") or "menu_estimate"
    if not isinstance(promotions, list) or not isinstance(combo_details, dict):
        raise MenuError("规划上下文的优惠或套餐详情格式错误")
    if price_basis not in ("menu_estimate", "live_quote", "demo_snapshot"):
        raise MenuError("报价依据格式错误")
    return store_code, promotions, combo_details, price_basis


def _check_menu(items: Sequence[MenuItemOut]) -> dict[str, MenuItemOut]:
    catalog: dict[str, MenuItemOut] = {}
    for item in items:
        if not isinstance(item, MenuItemOut) or not item.code or not item.name or item.code in catalog:
            raise MenuError("菜单商品必须有唯一非空 code 和名称")
        if type(item.price_fen) is not int or item.price_fen < 0:
            raise MenuError("菜单价格必须是非负整数分")
        if type(item.original_price_fen) is not int or item.original_price_fen < 0:
            raise MenuError("原价必须是非负整数分")
        catalog[item.code] = item
    return catalog


def _taste_tags(text: str) -> frozenset[str]:
    """只从已知单品/组成名称匹配食材或做法，绝不读取营销 tags。"""
    return frozenset(tag for tag, words in _TASTE_WORDS.items() if any(word in text for word in words))


def _confirmed_combo(item: MenuItemOut, detail: Any) -> tuple[int, int, int, str, list[dict], list[dict], int, str | None] | None:
    """套餐详情必须是已核实的最终选配；名称和默认人数不能代替组成。"""
    if not isinstance(detail, dict) or _get(detail, "verified") is not True:
        return None
    components = _get(detail, "components")
    selected = _get(detail, "selected_options", [])
    if not isinstance(components, list) or not components or not isinstance(selected, list):
        return None
    rounds = _get(detail, "rounds", [])
    if not isinstance(rounds, list):
        return None
    if selected and _get(detail, "selection_components_verified") is not True:
        # 仅有轮次代码并不能证明所报最终餐品就是这些选配。
        return None
    if rounds:
        for round_fact in rounds:
            round_id = _get(round_fact, "id")
            minimum = _get(round_fact, "min_select")
            maximum = _get(round_fact, "max_select")
            options = _get(round_fact, "options")
            if not round_id or type(minimum) is not int or type(maximum) is not int or minimum < 0 or (maximum != -1 and maximum < minimum) or not isinstance(options, list):
                return None
            allowed = {_get(option, "code") for option in options}
            selections = [option for option in selected if _get(option, "round_id") == round_id]
            count = sum(_get(option, "quantity", 0) for option in selections if type(_get(option, "quantity")) is int)
            if count < minimum or (maximum != -1 and count > maximum) or any(_get(option, "code") not in allowed or type(_get(option, "quantity")) is not int or _get(option, "quantity") < 1 for option in selections):
                return None
        if any(_get(option, "round_id") not in {_get(r, "id") for r in rounds} for option in selected):
            return None
    elif selected:
        # 没有轮次事实时无法证明所选规格属于套餐。
        return None
    main = safe = side = 0
    beverage_status = "none"
    component_names: list[str] = []
    for component in components:
        if not isinstance(component, dict) or not component.get("name") or not component.get("code"):
            return None
        quantity = component.get("quantity")
        servings = component.get("servings", 0)
        safe_servings = component.get("non_spicy_servings", 0)
        side_servings = component.get("side_servings", 0)
        role = component.get("food_role")
        beverage = component.get("beverage_status")
        if type(quantity) is not int or quantity < 1 or any(type(value) is not int or value < 0 for value in (servings, safe_servings, side_servings)):
            return None
        if safe_servings > servings:
            return None
        if role not in ("main", "side", "beverage", "dessert", "unknown") or beverage not in ("none", "contains", "unknown"):
            return None
        if role == "main" and (servings < 1 or _get(component, "servings_verified", detail["verified"]) is not True):
            return None
        if role != "main" and servings:
            return None
        if role == "side" and side_servings < 1:
            return None
        if role != "side" and side_servings:
            return None
        if role == "beverage" or beverage == "contains":
            beverage_status = "contains"
        elif beverage == "unknown" and beverage_status == "none":
            beverage_status = "unknown"
        main += servings * quantity
        safe += safe_servings * quantity
        side += side_servings * quantity
        component_names.append(component["name"])
    if main == 0 or safe > main:
        return None
    final_price = _get(detail, "price_fen", item.price_fen)
    if type(final_price) is not int or final_price < 0:
        return None
    if final_price == 0 and _get(detail, "price_verified", item.price_verified) is not True:
        return None
    if selected and "price_fen" not in detail:
        # 有选配时只有已核实的最终套餐价才可用于决策。
        return None
    spec = _get(detail, "spec")
    if spec is not None and not isinstance(spec, str):
        return None
    return main, safe, side, beverage_status, components, selected, final_price, spec


@dataclass(frozen=True)
class _Unit:
    item: MenuItemOut
    price_fen: int
    main: int
    safe: int
    side: int
    beverage_status: str
    components: list[dict]
    selected_options: list[dict]
    spec: str | None
    taste_tags: frozenset[str]
    max_quantity: int


def _unit_for(item: MenuItemOut, combo_details: Mapping[str, dict], requirements: Requirements) -> _Unit | None:
    if not item.available or not item.price_verified:
        return None
    # 自报持卡不等于已核实资格；无法证明资格时绝不采用会员低价。
    if item.requires_membership and not (requirements.membership_status == "active" and item.eligibility_verified):
        return None
    components: list[dict] = []
    selected: list[dict] = []
    spec = None
    price = item.price_fen
    if item.food_role == "combo" or item.is_combo:
        parsed = _confirmed_combo(item, combo_details.get(item.code))
        if parsed is None:
            return None
        main, safe, side, beverage, components, selected, price, spec = parsed
        taste_text = " ".join(component["name"] for component in components)
    elif item.food_role == "main":
        if not item.servings_verified or item.servings < 1 or item.non_spicy_servings > item.servings:
            return None
        main = item.servings
        safe = item.non_spicy_servings if item.spicy_level == "mild" else 0
        side = 0
        beverage = item.beverage_status
        taste_text = item.name
    elif item.food_role == "side":
        if item.side_servings < 1:
            return None
        main = safe = 0
        side = item.side_servings
        beverage = item.beverage_status
        taste_text = item.name
    else:
        return None
    if requirements.exclude_beverage and beverage != "none":
        return None
    if main == 0 and side == 0:
        return None
    maximum = item.max_quantity if item.max_quantity is not None else 60
    if maximum < 1:
        return None
    return _Unit(item, price, main, safe, side, beverage, components, selected, spec, _taste_tags(taste_text), maximum)


def _targets(requirements: Requirements) -> tuple[int, int, int, str]:
    people = requirements.people
    if requirements.appetite == "light":
        return people, requirements.non_spicy_people, 0, "轻饭量按每人 1 份主食配置"
    if requirements.appetite == "heavy":
        return people * 2, requirements.non_spicy_people * 2, ceil(people / 2), "大饭量按每人 2 份主食、每 2 人至少 1 份小食或额外主食配置"
    return people, requirements.non_spicy_people, ceil(people / 2), "正常饭量按每人 1 份主食、每 2 人至少 1 份小食或额外主食配置"


def _extra_portions(main: int, side: int, main_target: int) -> int:
    return side + max(0, main - main_target)


@dataclass(frozen=True)
class _State:
    counts: tuple[int, ...]
    cost: int
    main: int
    safe: int
    side: int
    taste: int


def _candidate_units(units: list[_Unit], preferred: set[str], promotions: list[dict], store_code: str) -> list[_Unit]:
    """限制搜索宽度，同时保留低价、不辣、口味、配菜和优惠关联候选。"""
    mains = [unit for unit in units if unit.main]
    sides = [unit for unit in units if unit.side]
    promo_codes = {code for promo in promotions if _get(promo, "verified") is True and _get(promo, "eligible") is True for code in (_get(promo, "item_codes", _get(promo, "item_ids", [])) or [])}
    groups = [
        # 优惠商品优先占位，避免多商品菜单将唯一折后可行项挤出搜索集。
        sorted((u for u in units if u.item.code in promo_codes), key=lambda unit: unit.price_fen)[:8],
        sorted((u for u in mains if u.safe), key=lambda unit: (unit.price_fen / unit.safe, unit.price_fen))[:6],
        sorted(sides, key=lambda unit: (unit.price_fen / unit.side, unit.price_fen))[:6],
        sorted(mains, key=lambda unit: (-len(unit.taste_tags & preferred), unit.price_fen))[:6] if preferred else [],
        sorted(mains, key=lambda unit: (unit.price_fen / unit.main, unit.price_fen))[:10],
    ]
    chosen: dict[str, _Unit] = {}
    for group in groups:
        for unit in group:
            chosen[unit.item.code] = unit
    return list(chosen.values())[:32]


def _search(units: list[_Unit], requirements: Requirements, promotions: list[dict], store_code: str) -> list[_State]:
    main_target, safe_target, side_target, _ = _targets(requirements)
    limit = requirements.total_budget_fen + maximum_discount(promotions, store_code)
    if not units:
        return []
    min_main = min((u.price_fen / u.main for u in units if u.main), default=float("inf"))
    min_safe = min((u.price_fen / u.safe for u in units if u.safe), default=float("inf"))
    min_side = min((u.price_fen / u.side for u in units if u.side), default=float("inf"))
    if min_main == float("inf") or (safe_target and min_safe == float("inf")):
        return []
    max_main = max(u.main for u in units)
    max_side = max(u.side for u in units)
    preferred = set(requirements.preferred_tags) & set(_TASTE_WORDS)
    start = _State((0,) * len(units), 0, 0, 0, 0, 0)
    states = [start]
    complete: dict[tuple[int, ...], _State] = {}

    def estimate(state: _State) -> tuple[float, int, int]:
        remaining = max(
            max(0, main_target - state.main) * min_main,
            max(0, safe_target - state.safe) * min_safe if safe_target else 0,
            max(0, side_target - _extra_portions(state.main, state.side, main_target)) * min(min_side, min_main) if side_target else 0,
        )
        return state.cost + remaining, -sum(count > 0 for count in state.counts), -state.taste

    # 每轮至多 560 个部分篮子；这是有限候选搜索，不声称全局最优。
    for _ in range(main_target + side_target + 1):
        next_states: dict[tuple[int, ...], _State] = {}
        for state in states:
            for index, unit in enumerate(units):
                if state.counts[index] >= unit.max_quantity:
                    continue
                if state.main >= main_target + side_target and unit.side == 0:
                    continue
                if state.side >= side_target and unit.main == 0:
                    continue
                cost = state.cost + unit.price_fen
                main = state.main + unit.main
                side = state.side + unit.side
                if cost > limit or main > main_target + side_target + max_main - 1 or side > side_target + max_side:
                    continue
                counts = list(state.counts)
                counts[index] += 1
                new = _State(tuple(counts), cost, main, state.safe + unit.safe, side, state.taste + len(unit.taste_tags & preferred))
                if main >= main_target and new.safe >= safe_target and _extra_portions(main, side, main_target) >= side_target:
                    complete[new.counts] = new
                else:
                    next_states[new.counts] = new
        if not next_states:
            break
        pool = list(next_states.values())
        cheapest = sorted(pool, key=estimate)[:380]
        tastiest = sorted(pool, key=lambda state: (-state.taste, *estimate(state)))[:90] if preferred else []
        diverse = sorted(pool, key=lambda state: (-sum(count > 0 for count in state.counts), *estimate(state)))[:90]
        states = list({state.counts: state for state in cheapest + tastiest + diverse}.values())[:560]
    return list(complete.values())


def _build_plan(state: _State, units: list[_Unit], requirements: Requirements, nutrition: dict[str, NutritionOut], context: Any, plan_type: str) -> PlanOut:
    store_code, promotions, _, price_basis = _context_parts(context)
    quantity = {unit.item.code: count for unit, count in zip(units, state.counts) if count}
    menu_total = sum(unit.price_fen * count for unit, count in zip(units, state.counts))
    payable, applied = apply_promotions(quantity, menu_total, promotions, store_code)
    plan_items: list[PlanItemOut] = []
    for unit, count in zip(units, state.counts):
        if not count:
            continue
        item = unit.item
        original = max(item.original_price_fen, unit.price_fen)
        plan_items.append(PlanItemOut(
            code=item.code, name=item.name, quantity=count,
            unit_price_fen=unit.price_fen, original_unit_price_fen=original,
            line_total_fen=unit.price_fen * count, servings=unit.main,
            spicy_level=item.spicy_level, image=item.image, spec=unit.spec,
            nutrition=None if item.food_role == "combo" else nutrition.get(item.code) or nutrition.get(item.name),
            food_role=item.food_role, beverage_status=unit.beverage_status,
            non_spicy_servings=unit.safe, side_servings=unit.side,
            components=unit.components, selected_options=unit.selected_options,
        ))
    subtotal = sum(item.original_unit_price_fen * item.quantity for item in plan_items)
    main_target, safe_target, side_target, assumption = _targets(requirements)
    checks = [
        ConstraintCheck(name="预算", satisfied=payable <= requirements.total_budget_fen, detail=f"应付 {payable} 分 / 预算 {requirements.total_budget_fen} 分"),
        ConstraintCheck(name="人数", satisfied=state.main >= main_target, detail=f"主食 {state.main} 份 / 需要 {main_target} 份"),
        ConstraintCheck(name="忌口", satisfied=state.safe >= safe_target, detail=f"已确认不辣 {state.safe} 份 / 需要 {safe_target} 份"),
        ConstraintCheck(name="加餐", satisfied=_extra_portions(state.main, state.side, main_target) >= side_target, detail=f"小食及额外主食 {_extra_portions(state.main, state.side, main_target)} 份 / 需要 {side_target} 份"),
        ConstraintCheck(name="不带饮料", satisfied=not requirements.exclude_beverage or all(item.beverage_status == "none" for item in plan_items), detail="已核实组合不含饮品" if requirements.exclude_beverage else "未限制饮品"),
        ConstraintCheck(name="资格", satisfied=True, detail="所用餐品资格均已确认或无需会员资格"),
    ]
    preferred = set(requirements.preferred_tags)
    known_preferred = preferred & set(_TASTE_WORDS)
    matched = known_preferred & set().union(*(unit.taste_tags for unit, count in zip(units, state.counts) if count))
    reasons = [assumption, f"主食 {state.main} 份、小食 {state.side} 份；已确认不辣主食 {state.safe} 份", "价格仅为本次已核实菜单与优惠的有限候选估算，最终报价以 A 核验为准"]
    if applied:
        reasons.append(f"采用 1 条已核实优惠：{applied[0]['description']}，减 {applied[0]['discount_fen']} 分")
    if known_preferred:
        reasons.append("口味从餐品名称及已核实组成匹配：" + ("、".join(sorted(matched)) if matched else "暂无匹配"))
    unknown_preferred = preferred - set(_TASTE_WORDS)
    if unknown_preferred:
        reasons.append("未识别口味偏好：" + "、".join(sorted(unknown_preferred)))
    if plan_type == "balanced":
        reasons.append("搭配多样指商品种类较多，不代表营养均衡")
    if plan_type == "cheapest":
        reasons.append("最省仅指本次有限候选中的较低应付")
    summary = f"{len(plan_items)} 种商品，主食 {state.main} 份、小食 {state.side} 份，应付 {payable / 100:.2f} 元"
    plan = PlanOut(
        plan_id="", type=plan_type, title=_TITLES[plan_type], summary=summary,
        items=plan_items, subtotal_fen=subtotal, discount_fen=subtotal - payable,
        total_fen=payable, budget_fen=requirements.total_budget_fen,
        remaining_fen=requirements.total_budget_fen - payable,
        servings_total=state.main, non_spicy_servings=state.safe,
        side_servings_total=state.side, reasons=reasons, checks=checks,
        price_basis=price_basis, applied_promotions=applied,
    )
    from .membership import compare_membership
    from .nutrition import summarize_nutrition

    basket = [{"code": item.code, "quantity": item.quantity, "food_role": item.food_role,
               "selected_options": [option.model_dump() for option in item.selected_options], "components": item.components} for item in plan_items]
    plan.membership_comparison = compare_membership(_get(context, "membership_facts"), requirements, basket)
    if nutrition:
        # 替换建议由下方实际生成并复核另一篮子后附加，不采信外部布尔勾选。
        plan.nutrition_summary = summarize_nutrition(plan_items, nutrition)
    return plan


def validate_plan(plan: PlanOut, items: Sequence[MenuItemOut], requirements: Requirements, context: Any = None) -> None:
    """A 可在保存前调用；不相信 B 输出的数量、金额、优惠和勾选状态。"""
    if not isinstance(plan, PlanOut) or not isinstance(requirements, Requirements):
        raise PlanValidationError("方案或需求类型不符合公共契约")
    catalog = _check_menu(items)
    store_code, promotions, combos, price_basis = _context_parts(context)
    if not plan.items or plan.price_basis != price_basis:
        raise PlanValidationError("方案为空或报价依据不一致")
    if plan.type not in _TITLES or not plan.summary.strip():
        raise PlanValidationError("方案类型或摘要不完整")
    quantities: dict[str, int] = {}
    main = safe = side = menu_total = subtotal = 0
    for line in plan.items:
        if line.code in quantities or line.code not in catalog or type(line.quantity) is not int or line.quantity < 1:
            raise PlanValidationError("商品重复、不属于本门店或数量无效")
        menu_item = catalog[line.code]
        unit = _unit_for(menu_item, combos, requirements)
        if unit is None or line.quantity > unit.max_quantity:
            raise PlanValidationError("商品资格、规格、饮品或餐份未核实")
        original = max(menu_item.original_price_fen, unit.price_fen)
        if (line.name != menu_item.name or line.unit_price_fen != unit.price_fen or line.original_unit_price_fen != original
                or line.line_total_fen != unit.price_fen * line.quantity or line.servings != unit.main
                or line.non_spicy_servings != unit.safe or line.side_servings != unit.side
                or line.food_role != menu_item.food_role or line.beverage_status != unit.beverage_status
                or line.spicy_level != menu_item.spicy_level or line.image != menu_item.image
                or line.components != unit.components
                or line.selected_options != [SelectedOptionOut.model_validate(option) for option in unit.selected_options]
                or line.spec != unit.spec):
            raise PlanValidationError("商品明细、套餐组成或价格与已核实事实不一致")
        quantities[line.code] = line.quantity
        main += unit.main * line.quantity
        safe += unit.safe * line.quantity
        side += unit.side * line.quantity
        menu_total += unit.price_fen * line.quantity
        subtotal += original * line.quantity
    payable, applied = apply_promotions(quantities, menu_total, promotions, store_code)
    main_target, safe_target, side_target, _ = _targets(requirements)
    if main < main_target or safe < safe_target or _extra_portions(main, side, main_target) < side_target or payable > requirements.total_budget_fen:
        raise PlanValidationError("方案未满足预算、餐份、饭量或不辣人数")
    if (plan.servings_total != main or plan.non_spicy_servings != safe or plan.side_servings_total != side
            or plan.subtotal_fen != subtotal or plan.total_fen != payable
            or plan.discount_fen != subtotal - payable or plan.remaining_fen != requirements.total_budget_fen - payable
            or plan.budget_fen != requirements.total_budget_fen or plan.applied_promotions != applied):
        raise PlanValidationError("方案汇总金额或餐份与明细不一致")
    if not plan.checks or not all(check.satisfied for check in plan.checks):
        raise PlanValidationError("方案约束检查不能包含失败项")


def generate_plans(
    items: list[MenuItemOut], requirements: Requirements, nutrition: dict[str, NutritionOut], *, context: Any = None
) -> tuple[list[PlanOut], str | None]:
    """A 的现有三参数入口；context 是扩展的可选已核实事实。"""
    if not isinstance(requirements, Requirements):
        raise TypeError("requirements 必须是 backend.schemas.Requirements")
    if not isinstance(nutrition, dict):
        raise TypeError("nutrition 必须是按商品 code 或名称索引的字典")
    _check_menu(items)
    store_code, promotions, combos, _ = _context_parts(context)
    available = [unit for item in items if (unit := _unit_for(item, combos, requirements)) is not None]
    main_target, safe_target, side_target, _ = _targets(requirements)
    if not any(unit.main for unit in available):
        return [], "没有餐份和规格已核实的可用主食；套餐组成未知时不能按名称猜人数"
    if safe_target and not any(unit.safe for unit in available):
        return [], "没有已确认的不辣主食，无法满足忌口人数"
    preferred = set(requirements.preferred_tags) & set(_TASTE_WORDS)
    units = _candidate_units(available, preferred, promotions, store_code)
    candidates = []
    for state in _search(units, requirements, promotions, store_code):
        quantities = {unit.item.code: count for unit, count in zip(units, state.counts) if count}
        payable, _ = apply_promotions(quantities, state.cost, promotions, store_code)
        if payable <= requirements.total_budget_fen:
            candidates.append((state, payable))
    if not candidates:
        return [], "在预算、餐份、饭量、忌口、饮品及资格约束下无已核实可行方案；可调整需求或补充套餐/报价事实"
    def variety(state: _State) -> int:
        return sum(count > 0 for count in state.counts)
    rankings = [
        ("cheapest", sorted(candidates, key=lambda pair: (pair[1], -variety(pair[0])))),
        ("balanced", sorted(candidates, key=lambda pair: (-variety(pair[0]), pair[1]))),
    ]
    if preferred:
        rankings.append(("taste", sorted(candidates, key=lambda pair: (-pair[0].taste, pair[1]))))
    chosen: list[PlanOut] = []
    chosen_states: list[_State] = []
    seen: set[tuple[int, ...]] = set()
    for plan_type, ranking in rankings:
        state, _ = ranking[0]
        # 同一篮子已代表该视角时不挑次优篮子凑卡；无口味命中也不标口味匹配。
        if state.counts in seen or (plan_type == "taste" and state.taste == 0):
            continue
        plan = _build_plan(state, units, requirements, nutrition, context, plan_type)
        validate_plan(plan, items, requirements, context)
        chosen.append(plan)
        chosen_states.append(state)
        seen.add(state.counts)
    if nutrition:
        from .nutrition import build_nutrition_suggestion

        alternatives = sorted(candidates, key=lambda pair: pair[1])[:48]
        for plan, source_state in zip(chosen, chosen_states):
            for alternative_state, _ in alternatives:
                if alternative_state.counts == source_state.counts or alternative_state.taste < source_state.taste:
                    continue
                alternative = _build_plan(alternative_state, units, requirements, nutrition, context, "cheapest")
                validate_plan(alternative, items, requirements, context)
                suggestion = build_nutrition_suggestion(plan, alternative)
                if suggestion is not None and plan.nutrition_summary is not None:
                    plan.nutrition_summary["suggestions"].append(suggestion)
                    break
    if len(chosen) == 1:
        chosen[0].reasons.append("当前已核实候选仅形成一套不同搭配，不重复凑卡")
    return chosen, None
