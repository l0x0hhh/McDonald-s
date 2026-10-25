"""Offline fallback regression tests for actual extra portions and whole-basket diet."""

import pytest

from backend.schemas import (
    ComboDetailOut, ComboOptionOut, ComboRoundOut, MenuItemOut, PlanningContext,
    Requirements,
)
from backend.services.sample_planner import generate_sample_plans


def main(code="main", *, price=1000, spicy="mild"):
    return MenuItemOut(
        code=code, name="双层吉士汉堡" if spicy == "mild" else "麦辣鸡腿汉堡",
        price_fen=price, original_price_fen=price, category="汉堡",
        food_role="main", beverage_status="none", spicy_level=spicy,
        servings=1, servings_verified=True, non_spicy_servings=int(spicy == "mild"),
    )


def side(*, role="side", spicy="mild"):
    return MenuItemOut(
        code="extra", name="中薯条" if role == "side" else "圆筒冰淇淋",
        price_fen=100, original_price_fen=100, category="小食甜品",
        food_role=role, beverage_status="none", spicy_level=spicy,
        servings=0, servings_verified=True, side_servings=int(role == "side"),
    )


def combo(*, extra_role="side", drink_name=None):
    item = MenuItemOut(
        code="combo", name="测试套餐", price_fen=1000, original_price_fen=1000,
        category="套餐", is_combo=True, food_role="combo",
    )
    choices = [
        ("main", "双层吉士汉堡", "main", "none"),
        ("extra", "中薯条" if extra_role == "side" else "圆筒冰淇淋", extra_role, "none"),
    ]
    if drink_name:
        choices.append(("drink", drink_name, "beverage", "contains"))
    detail = ComboDetailOut(
        code=item.code, name=item.name,
        rounds=[ComboRoundOut(
            round_id=code, name=code, min_quantity=1, max_quantity=1, default_quantity=1,
            options=[ComboOptionOut(code=code, name=name, food_role=role,
                                    beverage_status=beverage, diff_price_fen=0,
                                    is_default=True, max_quantity=1)],
        ) for code, name, role, beverage in choices],
    )
    return item, PlanningContext(combo_details={item.code: detail})


def test_normal_appetite_adds_actual_extra_main_when_no_side_exists():
    plans, reason = generate_sample_plans([main()], Requirements(people=2, budget_fen=3000), {})
    assert reason is None
    assert plans
    assert all(plan.servings_total == 3 and plan.side_servings_total == 0 for plan in plans)


def test_affordable_side_fills_normal_extra_target_and_is_reported():
    plans, reason = generate_sample_plans([main(), side()], Requirements(people=2, budget_fen=2100), {})
    assert reason is None
    assert plans
    for plan in plans:
        assert (plan.servings_total, plan.side_servings_total, plan.total_fen) == (2, 1, 2100)
        extra = next(item for item in plan.items if item.code == "extra")
        assert (extra.food_role, extra.side_servings, extra.non_spicy_servings) == ("side", 1, 0)


def test_dessert_does_not_fill_normal_extra_target():
    plans, reason = generate_sample_plans(
        [main(), side(role="dessert")], Requirements(people=2, budget_fen=2100), {},
    )
    assert plans == []
    assert reason


def test_combo_dessert_does_not_fill_normal_extra_target():
    item, context = combo(extra_role="dessert")
    plans, reason = generate_sample_plans([item], Requirements(people=1, budget_fen=1000), {}, context)
    assert plans == []
    assert reason


def test_combo_real_side_is_counted_without_unnecessary_extra_main():
    item, context = combo()
    plans, reason = generate_sample_plans([item], Requirements(people=1, budget_fen=1000), {}, context)
    assert reason is None
    assert len(plans) == 1
    assert (plans[0].servings_total, plans[0].side_servings_total, plans[0].total_fen) == (1, 1, 1000)
    assert len(plans[0].items[0].components) == 2


@pytest.mark.parametrize("spicy", ["spicy", "unknown"])
def test_everyone_non_spicy_rejects_unconfirmed_extra_side(spicy):
    requirements = Requirements(people=1, budget_fen=1100,
                                dietary_constraints=[{"kind": "non_spicy", "people": 1}])
    plans, reason = generate_sample_plans([main(), side(spicy=spicy)], requirements, {})
    assert plans == []
    assert reason


@pytest.mark.parametrize("drink_name", ["辣味可乐", "神秘饮料"])
def test_everyone_non_spicy_checks_beverage_inside_combo(drink_name):
    item, context = combo(drink_name=drink_name)
    requirements = Requirements(people=1, budget_fen=1000,
                                dietary_constraints=[{"kind": "non_spicy", "people": 1}])
    plans, reason = generate_sample_plans([item], requirements, {}, context)
    assert plans == []
    assert reason


def test_mixed_diet_keeps_existing_main_guarantee_not_whole_basket_rule():
    item, context = combo(drink_name="神秘饮料")
    requirements = Requirements(people=2, budget_fen=2000,
                                dietary_constraints=[{"kind": "non_spicy", "people": 1}])
    plans, reason = generate_sample_plans([item], requirements, {}, context)
    assert reason is None
    assert plans
    assert all(plan.non_spicy_servings >= 1 for plan in plans)


def test_everyone_non_spicy_extra_main_is_also_mild():
    requirements = Requirements(people=1, budget_fen=2000,
                                dietary_constraints=[{"kind": "non_spicy", "people": 1}])
    plans, reason = generate_sample_plans([main(), main("spicy", price=100, spicy="spicy")], requirements, {})
    assert reason is None
    assert plans
    assert all(plan.servings_total == 2 for plan in plans)
    assert all(item.spicy_level == "mild" for plan in plans for item in plan.items)
