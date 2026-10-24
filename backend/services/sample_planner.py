"""演示样例方案生成器（A 维护的接口样例兜底）。

⚠️ 边界声明：这不是正式算法。正式的组合、优惠与排序逻辑属于 B 的
backend/skill/ 模块（契约见 plan_service.py 顶部注释）。本文件存在的目的：
1. 让 C 的前端在没有 B 模块时也能串通「选店 → 需求 → 方案 → 结算」全流程；
2. 给 B 提供一份可运行的契约参考实现。

它的所有产出都会在 PlanResponse.suggestions 里标注 demo_sample，
绝不冒充正式算法。已知简化：不处理优惠叠加、不考虑口味偏好以外的偏好、
不做营养均衡优化。
"""

from __future__ import annotations

import uuid
from typing import Optional

from ..schemas import (
    ConstraintCheck,
    MenuItemOut,
    NutritionOut,
    PlanItemOut,
    PlanOut,
    Requirements,
)

TITLES = {
    "cheapest": "最省方案",
    "balanced": "搭配均衡方案",
    "taste": "口味匹配方案",
}


def _attach_nutrition(item: PlanItemOut, nutrition: dict[str, NutritionOut]) -> None:
    item.nutrition = nutrition.get(item.name) or NutritionOut(matched=False)


def _make_item(
    menu_item: MenuItemOut, quantity: int, nutrition: dict[str, NutritionOut]
) -> PlanItemOut:
    return PlanItemOut(
        code=menu_item.code,
        name=menu_item.name,
        quantity=quantity,
        unit_price_fen=menu_item.price_fen,
        original_unit_price_fen=menu_item.original_price_fen,
        line_total_fen=menu_item.price_fen * quantity,
        servings=menu_item.servings,
        spicy_level=menu_item.spicy_level,
        image=menu_item.image,
    )


def _fill_servings(
    candidates: list[MenuItemOut],
    needed: int,
    max_repeat: int,
) -> list[tuple[MenuItemOut, int]]:
    """按候选顺序（调用方已排序）填满 needed 人份，单品种不超过 max_repeat。"""
    result: list[tuple[MenuItemOut, int]] = []
    remaining = needed
    for item in candidates:
        if remaining <= 0:
            break
        take = min(max_repeat, remaining)
        result.append((item, take))
        remaining -= take
    return result


def _checks(
    items: list[PlanItemOut], requirements: Requirements, total_fen: int
) -> list[ConstraintCheck]:
    servings_total = sum(i.servings * i.quantity for i in items)
    non_spicy = sum(
        i.servings * i.quantity for i in items if i.spicy_level == "mild"
    )
    return [
        ConstraintCheck(
            name="预算",
            satisfied=total_fen <= requirements.total_budget_fen,
            detail=f"应付 {total_fen / 100:.1f} 元 / 预算 {requirements.total_budget_fen / 100:.1f} 元",
        ),
        ConstraintCheck(
            name="人数",
            satisfied=servings_total >= requirements.people,
            detail=f"可满足 {servings_total} 人份 / 共 {requirements.people} 人",
        ),
        ConstraintCheck(
            name="忌口",
            satisfied=non_spicy >= requirements.non_spicy_people,
            detail=f"不辣餐份 {non_spicy} / 需要 {requirements.non_spicy_people}",
        ),
        ConstraintCheck(
            name="无需开卡",
            satisfied=True,
            detail="本样例只选用无需麦金卡的商品，避开随单购差价",
        ),
    ]


def _build_plan(
    plan_type: str,
    selection: list[tuple[MenuItemOut, int]],
    requirements: Requirements,
    nutrition: dict[str, NutritionOut],
    extra_reasons: list[str],
) -> PlanOut:
    items = [_make_item(m, q, nutrition) for m, q in selection]
    for item in items:
        _attach_nutrition(item, nutrition)
    subtotal = sum(i.original_unit_price_fen * i.quantity for i in items)
    total = sum(i.line_total_fen for i in items)
    servings_total = sum(i.servings * i.quantity for i in items)
    non_spicy = sum(i.servings * i.quantity for i in items if i.spicy_level == "mild")

    reasons = [
        f"共 {servings_total} 人份，其中不辣餐份 {non_spicy} 份",
        f"按菜单现价合计 {total / 100:.1f} 元（原价口径 {subtotal / 100:.1f} 元）",
        *extra_reasons,
    ]
    return PlanOut(
        plan_id=uuid.uuid4().hex[:12],
        type=plan_type,  # type: ignore[arg-type]
        title=TITLES[plan_type],
        summary="",
        items=items,
        subtotal_fen=subtotal,
        discount_fen=subtotal - total,
        total_fen=total,
        budget_fen=requirements.total_budget_fen,
        remaining_fen=requirements.total_budget_fen - total,
        servings_total=servings_total,
        non_spicy_servings=non_spicy,
        reasons=reasons,
        checks=_checks(items, requirements, total),
    )


def _tag_score(item: MenuItemOut, preferred_tags: list[str]) -> int:
    return len(set(item.tags) & set(preferred_tags))


def generate_sample_plans(
    items: list[MenuItemOut],
    requirements: Requirements,
    nutrition: dict[str, NutritionOut],
) -> tuple[list[PlanOut], Optional[str]]:
    """返回 (方案列表, 不可满足原因)。原因非空时列表为空。"""
    # 只用无需开卡的套餐作为「一人一份」的候选
    eligible = [i for i in items if i.is_combo and not i.requires_membership]
    mild = sorted(
        [i for i in eligible if i.spicy_level == "mild"], key=lambda i: i.price_fen
    )
    others = sorted(
        [i for i in eligible if i.spicy_level != "mild"], key=lambda i: i.price_fen
    )
    if not eligible:
        return [], "该门店菜单中没有可用于组合的套餐，无法生成方案"

    need_mild = requirements.non_spicy_people
    need_rest = requirements.people - need_mild

    if not mild and need_mild > 0:
        return [], "菜单中缺少可确认不辣的餐品，无法满足忌口要求；建议放宽忌口或更换门店"

    # 最省：忌口的人用最便宜的不辣套餐，其余用最便宜套餐
    cheapest_selection: list[tuple[MenuItemOut, int]] = []
    if need_mild > 0:
        cheapest_selection += _fill_servings(mild, need_mild, need_mild)
    if need_rest > 0:
        pool = others if others else mild
        cheapest_selection += _fill_servings(pool, need_rest, need_rest)
    plan_cheapest = _build_plan(
        "cheapest",
        cheapest_selection,
        requirements,
        nutrition,
        ["最省口径：候选套餐中应付金额最低（不含需麦金卡的商品）"],
    )
    if plan_cheapest.total_fen > requirements.total_budget_fen:
        return [], (
            f"最便宜的可行组合也要 {plan_cheapest.total_fen / 100:.1f} 元，"
            f"超出预算 {requirements.total_budget_fen / 100:.1f} 元；"
            "建议提高预算或减少人数"
        )

    # 搭配均衡：单品种最多 2 份，优先覆盖不同商品
    balanced_selection: list[tuple[MenuItemOut, int]] = []
    if need_mild > 0:
        balanced_selection += _fill_servings(mild, need_mild, 2)
    if need_rest > 0:
        pool = others if others else mild
        balanced_selection += _fill_servings(pool, need_rest, 2)
    plan_balanced = _build_plan(
        "balanced",
        balanced_selection,
        requirements,
        nutrition,
        ["均衡口径：每个商品最多 2 份，尽量覆盖不同主食"],
    )
    if plan_balanced.total_fen > requirements.total_budget_fen:
        plan_balanced = plan_cheapest.model_copy(deep=True)
        plan_balanced.type = "balanced"
        plan_balanced.title = TITLES["balanced"]
        plan_balanced.reasons = [
            *plan_cheapest.reasons,
            "均衡组合超出预算，回退为最省组合",
        ]

    # 口味匹配：优先标签命中的商品
    taste_mild = sorted(
        [i for i in eligible if i.spicy_level == "mild"],
        key=lambda i: (-_tag_score(i, requirements.preferred_tags), i.price_fen),
    )
    taste_others = sorted(
        [i for i in eligible if i.spicy_level != "mild"],
        key=lambda i: (-_tag_score(i, requirements.preferred_tags), i.price_fen),
    )
    taste_selection: list[tuple[MenuItemOut, int]] = []
    if need_mild > 0:
        taste_selection += _fill_servings(taste_mild or mild, need_mild, 3)
    if need_rest > 0:
        pool = taste_others or taste_mild or mild
        taste_selection += _fill_servings(pool, need_rest, 3)
    plan_taste = _build_plan(
        "taste",
        taste_selection,
        requirements,
        nutrition,
        [
            "口味口径：优先选择与偏好标签匹配的商品（不突破忌口与预算）"
        ],
    )
    if plan_taste.total_fen > requirements.total_budget_fen:
        plan_taste = plan_balanced.model_copy(deep=True)
        plan_taste.type = "taste"
        plan_taste.title = TITLES["taste"]
        plan_taste.reasons = [*plan_balanced.reasons, "口味组合超出预算，回退为均衡组合"]

    plans = [plan_cheapest, plan_balanced, plan_taste]
    # 去重：三个口径撞出同一组合时只留一个，不凑数
    unique: list[PlanOut] = []
    seen: set[tuple] = set()
    for plan in plans:
        key = tuple(sorted((i.code, i.quantity) for i in plan.items))
        if key in seen:
            continue
        seen.add(key)
        unique.append(plan)
    if len(unique) == 1:
        unique[0].reasons.append("当前候选下三种口径结果相同，仅返回一套并如实说明")
    return unique, None
