# 整合说明：增加 A/B 套餐差价接线与算法失败拒绝场景；本轮未执行。
"""事实边界回归用例，全部使用内存夹具，不请求真实 MCP。"""

import asyncio
from types import SimpleNamespace

import pytest

from backend.providers.mapper import map_meal_detail, map_menu
from backend.providers.mcp_provider import parse_price_fen
from backend.schemas import (
    MenuItemOut, MenuOut, PlanItemOut, PlanOut, PlanRequest, Requirements, SelectedOptionOut,
)
from backend.services.capabilities import skill_supports_context, static_capabilities
from backend.services.plan_validator import validate_plan


def combo_fixture():
    item = MenuItemOut(code="combo", name="随心配", price_fen=1000,
                       original_price_fen=1200, category="套餐", is_combo=True)
    detail = map_meal_detail({"data": {
        "code": "combo", "name": "随心配", "supportModify": True,
        "rounds": [
            {"id": "blue", "name": "蓝区", "quantity": 1, "minQuantity": 1, "maxQuantity": 1,
             "choices": [{"code": "burger", "name": "双层吉士汉堡", "isDefault": True,
                          "quantity": 1, "maxQuantity": 1, "diffPrice": "+ ¥0"}]},
            {"id": "pink", "name": "粉区", "quantity": 1, "minQuantity": 1, "maxQuantity": 1,
             "choices": [
                 {"code": "cola", "name": "可乐中杯", "isDefault": True,
                  "quantity": 0, "maxQuantity": 1, "diffPrice": "+ ¥0"},
                 {"code": "fries", "name": "中薯条", "isDefault": False,
                  "quantity": 0, "maxQuantity": 1, "diffPrice": "+ ¥2"},
             ]},
        ],
    }}, "demo_snapshot")
    return item, detail


def food_selection():
    return [SelectedOptionOut(round_id="blue", option_code="burger", diff_price_fen=0),
            SelectedOptionOut(round_id="pink", option_code="fries", diff_price_fen=200)]


def one_item_plan(item, *, selected=None, extra=0, quantity=1, servings=1):
    unit = item.price_fen + extra
    original = item.original_price_fen + extra
    return PlanOut(
        plan_id="fixture", type="cheapest", title="夹具", summary="",
        items=[PlanItemOut(code=item.code, name=item.name, quantity=quantity,
                           unit_price_fen=unit, original_unit_price_fen=original,
                           line_total_fen=unit * quantity, servings=servings,
                           spicy_level="mild", selected_options=selected or [])],
        subtotal_fen=original * quantity, total_fen=unit * quantity,
        discount_fen=(original - unit) * quantity, budget_fen=5000,
        remaining_fen=5000 - unit * quantity, servings_total=servings * quantity,
        non_spicy_servings=servings * quantity,
    )


def validate_combo(plan, item, detail, *, exclude=True):
    return validate_plan(plan, Requirements(people=1, budget_fen=5000, exclude_beverage=exclude),
                         {item.code: item}, {item.code: detail})


def test_default_drinks_in_generic_rounds_are_rejected():
    item, detail = combo_fixture()
    assert detail.beverage_status == "contains"
    assert detail.servings_verified is False
    errors = validate_combo(one_item_plan(item), item, detail)
    assert any("饮品" in error for error in errors)


def test_explicit_food_replacement_with_surcharge_is_accepted():
    item, detail = combo_fixture()
    plan = one_item_plan(item, selected=food_selection(), extra=200, quantity=2)
    assert validate_combo(plan, item, detail) == []


def test_surcharge_must_be_included_in_unit_price():
    item, detail = combo_fixture()
    plan = one_item_plan(item, selected=food_selection())
    assert any("单价" in error for error in validate_combo(plan, item, detail))


@pytest.mark.parametrize("case", ["missing", "duplicate", "zero", "negative", "too_many", "wrong_diff", "unknown_option"])
def test_invalid_selections_are_rejected(case):
    item, detail = combo_fixture()
    selected = food_selection()
    if case == "missing":
        selected.pop()
    elif case == "duplicate":
        selected.append(selected[1].model_copy())
    elif case in ("zero", "negative", "too_many"):
        selected[1].quantity = {"zero": 0, "negative": -1, "too_many": 2}[case]
    elif case == "wrong_diff":
        selected[1].diff_price_fen = 0
    else:
        selected[1].option_code = "not_in_menu"
    assert validate_combo(one_item_plan(item, selected=selected, extra=200), item, detail)


def test_unknown_surcharge_cannot_be_assumed_free():
    item, detail = combo_fixture()
    detail.rounds[1].options[1].diff_price_fen = None
    assert any("差价未知" in error for error in validate_combo(
        one_item_plan(item, selected=food_selection(), extra=200), item, detail))


def test_mixed_default_portions_use_round_quantity():
    # 选项 quantity=0 不能漏掉饮料；同时轮次为两份时也不能缩成一份。
    from backend.services.meal_facts import resolve_meal_facts
    item, detail = combo_fixture()
    round_ = detail.rounds[0]
    round_.min_quantity = round_.max_quantity = round_.default_quantity = 2
    round_.options[0].max_quantity = 2
    facts, errors = resolve_meal_facts(item, detail)
    assert errors == []
    assert facts.servings == 2
    assert facts.beverage_status == "contains"


def test_side_cannot_fake_six_non_spicy_meals():
    side = MenuItemOut(code="fries", name="中薯条", price_fen=1000, original_price_fen=1000,
                       category="小食", food_role="side", beverage_status="none", spicy_level="mild")
    plan = one_item_plan(side, servings=6)
    errors = validate_plan(plan, Requirements(people=6, budget_fen=5000), {side.code: side}, {})
    assert any("实际主食餐份为 0" in error for error in errors)
    assert any("不辣餐份" in error for error in errors)


def test_spicy_choice_cannot_be_reported_as_mild():
    item, detail = combo_fixture()
    detail.rounds[0].options[0].name = "麦辣鸡腿汉堡"
    errors = validate_combo(one_item_plan(item, selected=food_selection(), extra=200), item, detail)
    assert any("辣度" in error for error in errors)
    assert any("不辣餐份" in error for error in errors)


@pytest.mark.parametrize("field", ["subtotal_fen", "discount_fen", "budget_fen", "remaining_fen", "servings_total", "non_spicy_servings"])
def test_forged_summary_fields_are_rejected(field):
    item, detail = combo_fixture()
    plan = one_item_plan(item, selected=food_selection(), extra=200)
    setattr(plan, field, getattr(plan, field) + 1)
    assert validate_combo(plan, item, detail)


def test_unknown_prices_are_excluded_and_real_zero_is_preserved():
    values = {"missing": None, "invalid": "oops", "infinity": "Infinity", "negative": "-1", "free": "0"}
    raw = {"categories": [{"name": "主食", "meals": [{"code": code} for code in values]}],
           "meals": {code: {"name": "吉士汉堡", "currentPrice": value, "originalPrice": None}
                     for code, value in values.items()}}
    items, _ = map_menu("store", raw)
    assert [(item.code, item.price_fen) for item in items] == [("free", 0)]
    assert items[0].original_price_fen == 0
    assert parse_price_fen("Infinity") is None


def test_context_signature_does_not_enable_features():
    def generate(items, requirements, nutrition, *, context=None):
        return [], None
    module = SimpleNamespace(generate_plans=generate)
    assert skill_supports_context(module)
    assert static_capabilities(skill_module=module)["exclude_beverage"] is False
    module.CAPABILITIES = {"exclude_beverage": True, "appetite_planning": True,
                           "taste_matching": "true", "membership_comparison": True}
    caps = static_capabilities(skill_module=module)
    assert caps["exclude_beverage"] and caps["appetite_planning"]
    assert not caps["taste_matching"] and not caps["membership_comparison"]


def test_positional_only_context_is_not_passed_as_keyword():
    def generate(items, requirements, nutrition, context=None, /):
        return [], None
    assert not skill_supports_context(SimpleNamespace(generate_plans=generate))


# 保留 B 已有三参数入口，无需为了 A 的校验调整位置参数。
def test_legacy_three_parameter_skill_remains_callable():
    from backend.schemas import PlanningContext
    from backend.services.plan_service import PlanService
    calls = []
    def legacy(items, requirements, nutrition):
        calls.append(requirements.people)
        return [], None
    service = PlanService(menus=object(), nutrition=object(), store=object(), combos=object())
    service._skill_checked = True
    service._skill_module = SimpleNamespace(generate_plans=legacy)
    result = asyncio.run(service._invoke_skill([], Requirements(people=1, budget_fen=5000), {},
                                              PlanningContext(store_code="store")))
    assert result == ([], None)
    assert calls == [1]


def test_skill_failure_cannot_bypass_appetite_and_taste_constraints(tmp_path):
    from backend.services.combo_service import ComboDetailsResult
    from backend.services.plan_service import PlanService
    from backend.services.plan_store import PlanStore
    item = MenuItemOut(code="burger", name="双层吉士汉堡", price_fen=1000, original_price_fen=1000,
                       category="汉堡", food_role="main", beverage_status="none", spicy_level="mild")
    async def menu(store_code):
        return MenuOut(store_code=store_code, items=[item], categories=[], data_source="demo_snapshot", fetched_at="")
    async def nutrition():
        return {}
    async def combos(store_code, codes):
        return ComboDetailsResult()
    def broken(items, requirements, nutrition, *, context=None):
        raise RuntimeError("fixture failure")
    service = PlanService(menus=SimpleNamespace(get_menu=menu),
                          nutrition=SimpleNamespace(get_nutrition_map=nutrition),
                          combos=SimpleNamespace(get_details=combos), store=PlanStore(tmp_path / "plans.json"))
    service._skill_checked = True
    service._skill_module = SimpleNamespace(generate_plans=broken, CAPABILITIES={
        "exclude_beverage": True, "appetite_planning": True, "taste_matching": True,
    })
    from backend.services.capabilities import CapabilityUnavailable
    with pytest.raises(CapabilityUnavailable, match="正式方案生成或约束校验失败"):
        asyncio.run(service.create_plans(PlanRequest(store_code="store", requirements=Requirements(
            people=1, budget_fen=5000, appetite="heavy", preferred_tags=["牛肉"], exclude_beverage=True,
        ))))
    assert not service.store._records


def test_adapted_default_combo_survives_both_validators_with_surcharge():
    from backend.schemas import PlanningContext
    from backend.services.skill_adapter import adapt_skill_input
    from backend.skill import generate_plans, validate_plan as validate_decision

    item, detail = combo_fixture()
    item.food_role = "combo"
    detail.rounds[1].options[0].diff_price_fen = 200
    detail.rounds[1].max_quantity = -1
    context = PlanningContext(store_code="store", combo_details={item.code: detail},
                              price_basis="demo_snapshot")
    requirements = Requirements(people=1, budget_fen=5000, appetite="light")
    adapted, facts = adapt_skill_input([item], context)
    plans, reason = generate_plans(adapted, requirements, {}, context=facts)
    assert reason is None and plans
    plan = plans[0]
    assert plan.total_fen == 1200 and plan.subtotal_fen == 1400
    assert item.original_price_fen == 1200, "适配不能污染 A 的原菜单"
    assert plan.items[0].selected_options[1].option_code == "cola"
    validate_decision(plan, adapted, requirements, context=facts)
    assert validate_plan(plan, requirements, {item.code: item}, {item.code: detail},
                         context=context, strict_details=True) == []


@pytest.mark.parametrize("difference", [None, -2000])
def test_invalid_default_surcharge_is_excluded_before_skill_search(difference):
    from backend.schemas import PlanningContext
    from backend.services.skill_adapter import adapt_skill_input
    from backend.skill import generate_plans

    item, detail = combo_fixture()
    detail.rounds[1].options[0].diff_price_fen = difference
    adapted, facts = adapt_skill_input([item], PlanningContext(
        store_code="store", combo_details={item.code: detail}))
    plans, reason = generate_plans(adapted, Requirements(people=1, budget_fen=5000), {}, context=facts)
    assert plans == [] and reason
