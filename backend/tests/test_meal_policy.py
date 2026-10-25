"""Dining-policy regressions: real planner, facts, and independent validator."""
import pytest

from backend.providers.mapper import map_meal_detail
from backend.schemas import MenuItemOut, PlanningContext, Requirements
from backend.services.meal_facts import resolve_meal_facts
from backend.services.plan_validator import validate_plan as validate_facts
from backend.services.skill_adapter import adapt_skill_input
from backend.skill import generate_plans, validate_plan, PlanValidationError


def food(code, role="main", level="mild", price=1000):
    name = {"main": "吉士汉堡", "side": "中薯条", "dessert": "香草圆筒"}[role]
    return MenuItemOut(code=code, name=name, price_fen=price, original_price_fen=price,
                       category="测试", food_role=role, beverage_status="none",
                       spicy_level=level, servings=int(role == "main"), servings_verified=True,
                       non_spicy_servings=int(role == "main" and level == "mild"),
                       side_servings=int(role == "side"))


def request(non_spicy=2, **changes):
    return Requirements(people=2, budget_fen=6000,
                        dietary_constraints=[{"kind": "non_spicy", "people": non_spicy}], **changes)


@pytest.mark.parametrize("role", ["side", "main"])
@pytest.mark.parametrize("level", ["spicy", "unknown"])
def test_all_non_spicy_filters_every_extra_item(role, level):
    menu = [food("main"), food("side", "side", price=300), food("bad", role, level, 50)]
    plans, reason = generate_plans(menu, request(), {})
    assert reason is None and plans
    assert all(line.code != "bad" for plan in plans for line in plan.items)
    assert plans[0].total_fen == 2300


def test_mixed_group_keeps_spicy_options_without_losing_mild_main():
    menu = [food("mild"), food("spicy", level="spicy", price=500), food("side", "side", price=300)]
    plans, _ = generate_plans(menu, request(non_spicy=1), {})
    assert plans[0].total_fen == 1800
    assert plans[0].non_spicy_servings == 1
    assert any(line.code == "spicy" for line in plans[0].items)


@pytest.mark.parametrize("appetite,main_count,side_count", [("light", 2, 0), ("normal", 2, 1), ("heavy", 4, 1)])
def test_individual_dessert_never_replaces_a_meal_extra(appetite, main_count, side_count):
    menu = [food("main"), food("side", "side", price=300), food("cone", "dessert", price=50)]
    plans, reason = generate_plans(menu, request(appetite=appetite), {})
    assert reason is None and plans
    first = plans[0]
    assert (first.servings_total, first.side_servings_total) == (main_count, side_count)
    assert all(line.code != "cone" for plan in plans for line in plan.items)


def test_low_budget_does_not_use_dessert_to_fake_enough_food():
    req = request().model_copy(update={"budget_fen": 2050})
    plans, reason = generate_plans([food("main"), food("cone", "dessert", price=50)], req, {})
    assert not plans and reason


def test_two_validators_reject_spicy_extra_even_with_enough_mild_mains():
    menu = [food("main"), food("hot", "side", "spicy", 100)]
    unrestricted = request(non_spicy=0)
    plan = generate_plans(menu, unrestricted, {})[0][0]
    assert plan.non_spicy_servings == 2
    with pytest.raises(PlanValidationError):
        validate_plan(plan, menu, request())
    errors = validate_facts(plan, request(), {i.code: i for i in menu}, {}, strict_details=True)
    assert any("全员不辣" in error for error in errors)


def fixed_combo(component_level, role="side"):
    combo = MenuItemOut(code="combo", name="已选套餐", price_fen=100, original_price_fen=100,
                        category="套餐", food_role="combo", is_combo=True, spicy_level="mild")
    component = {"code": "extra", "name": "已选配品", "quantity": 1, "food_role": role,
                 "beverage_status": "contains" if role == "beverage" else "none",
                 "side_servings": int(role == "side")}
    if component_level is not None:
        component["spicy_level"] = component_level
    detail = {"verified": True, "components": [
        {"code": "burger", "name": "吉士汉堡", "quantity": 1, "food_role": "main",
         "beverage_status": "none", "servings": 1, "non_spicy_servings": 1, "spicy_level": "mild"}, component]}
    return combo, PlanningContext(combo_details={"combo": detail})


@pytest.mark.parametrize("role", ["side", "dessert", "beverage"])
@pytest.mark.parametrize("level", ["spicy", "unknown", None])
def test_combo_top_level_mild_does_not_hide_unverified_component(role, level):
    combo, context = fixed_combo(level, role)
    menu = [combo, food("main"), food("side", "side", price=300)]
    plans, _ = generate_plans(menu, request(), {}, context=context)
    assert plans
    assert all(line.code != "combo" for p in plans for line in p.items)


def test_confirmed_mild_combo_still_allowed():
    combo, context = fixed_combo("mild")
    plans, reason = generate_plans([combo], request(), {}, context=context)
    assert reason is None and plans[0].total_fen == 200


def selected_combo(extra_name="香草圆筒"):
    item = MenuItemOut(code="set", name="汉堡套餐", price_fen=1000, original_price_fen=1000,
                       category="套餐", food_role="combo", is_combo=True)
    detail = map_meal_detail({"code": "set", "name": "汉堡套餐", "rounds": [
        {"id": rid, "name": rid, "quantity": 1, "minQuantity": 1, "maxQuantity": 1,
         "choices": [{"code": rid, "name": name, "isDefault": True, "maxQuantity": 1, "diffPrice": "+ ¥0"}]}
        for rid, name in [("main", "吉士汉堡"), ("extra", extra_name)]
    ]}, "demo_snapshot")
    return item, detail


def test_canonical_components_include_their_own_spicy_level():
    item, detail = selected_combo("辣味可乐")
    facts, errors = resolve_meal_facts(item, detail)
    assert errors == []
    assert [c.get("spicy_level") for c in facts.components] == ["mild", "spicy"]


@pytest.mark.parametrize("name", ["辣味可乐", "神秘汽水"])
def test_a_validator_independently_rejects_unsafe_combo_component(name):
    item, detail = selected_combo(name)
    # A supplied beverage role also covers drinks without known taste keywords.
    detail.rounds[1].options[0].food_role = "beverage"
    detail.rounds[1].options[0].beverage_status = "contains"
    context = PlanningContext(combo_details={item.code: detail})
    menu, payload = adapt_skill_input([item], context)
    plan = generate_plans(menu, request(non_spicy=0), {}, context=payload)[0][0]
    errors = validate_facts(plan, request(), {item.code: item}, {item.code: detail}, strict_details=True)
    assert any("全员不辣" in error for error in errors)


def test_strict_a_validator_recomputes_appetite_not_just_number_of_mains():
    menu = [food("main")]
    plan = generate_plans(menu, request(appetite="light"), {})[0][0]
    errors = validate_facts(plan, request(), {i.code: i for i in menu}, {}, strict_details=True)
    assert any("加餐" in error for error in errors)
