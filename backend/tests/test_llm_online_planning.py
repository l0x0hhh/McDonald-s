"""Model decisions, strict fact reconstruction, and online-only orchestration."""
import asyncio
import copy
import importlib
import json
from types import SimpleNamespace

import pytest

from backend.schemas import MenuItemOut, MenuOut, PlanRequest, PlanningContext, Requirements
from backend.providers.mapper import map_meal_detail, map_menu
from backend.services.plan_service import PlanService
from backend.services.plan_store import PlanStore


def run(coro):
    return asyncio.run(coro)


def planner_module():
    return importlib.import_module("backend.services.llm_planner")


def burger(code="burger", price=1000):
    return MenuItemOut(code=code, name="双层吉士汉堡", price_fen=price,
                       original_price_fen=price, category="汉堡", food_role="main",
                       beverage_status="none", spicy_level="mild", servings_verified=True)


def candidate(code="burger", quantity=1, title="模型选择", selected=None):
    return {"title": title, "summary": "汉堡分给不吃辣的人", "reasons": ["每人一个汉堡"],
            "items": [{"code": code, "quantity": quantity, "selected_options": selected or []}]}


class Model:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, payload, timeout):
        self.calls.append((copy.deepcopy(payload), timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_planner(model):
    return planner_module().LLMPlanner(requester=model)


def context(details=None):
    return PlanningContext(store_code="store", data_source="live_mcp", combo_details=details or {})


def combo():
    item = MenuItemOut(code="combo", name="随心配", price_fen=1000, original_price_fen=1000,
                       category="套餐", is_combo=True, food_role="combo")
    detail = map_meal_detail({"code": "combo", "name": "随心配", "rounds": [
        {"id": "main", "name": "主食", "minQuantity": 1, "maxQuantity": 1, "quantity": 1,
         "choices": [{"code": "burger", "name": "双层吉士汉堡", "isDefault": True, "diffPrice": None},
                     {"code": "fish", "name": "麦香鱼", "isDefault": False, "diffPrice": "+ ¥2"}]},
    ]}, "live_mcp")
    return item, detail


def selected(code):
    return [{"round_id": "main", "option_code": code, "quantity": 1}]


class Quote:
    async def quote(self, plan, store_code, requirements):
        assert plan.plan_id and store_code == "store"
        plan.total_fen = 800
        plan.remaining_fen = requirements.total_budget_fen - 800
        plan.price_basis = "live_quote"
        plan.quote_id = "official-quote"
        plan.quoted_at = "2026-10-25T00:00:00Z"
        plan.take_way_options = [{"code": "pickup", "title": "自取"}]
        return plan


def service(tmp_path, model, items=None, checkout=None):
    async def menu(code):
        return MenuOut(store_code=code, items=items or [burger()], categories=[],
                       data_source="live_mcp", fetched_at="now")
    async def nutrition():
        return {}
    return PlanService(menus=SimpleNamespace(get_menu=menu),
                       nutrition=SimpleNamespace(get_nutrition_map=nutrition),
                       store=PlanStore(tmp_path / "plans.json"),
                       planner=make_planner(model), checkout=checkout or Quote())


def test_model_order_and_number_are_real_baskets_not_old_search(monkeypatch, tmp_path):
    import backend.skill.planner as old
    import backend.services.sample_planner as sample
    def forbidden(*args, **kwargs):
        pytest.fail("old search/sample was called")
    monkeypatch.setattr(old, "_search", forbidden)
    monkeypatch.setattr(sample, "generate_sample_plans", forbidden)
    model = Model({"candidates": [candidate("b", title="先鱼"), candidate("a", title="后牛")]})
    svc = service(tmp_path, model, [burger("a"), burger("b")])
    out = run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=5000))))
    assert [p.title for p in out.plans] == ["先鱼", "后牛"]
    assert [p.items[0].code for p in out.plans] == ["b", "a"]
    assert [p.type for p in out.plans] == ["recommended", "alternative"]
    assert out.algorithm_source == "llm_skill" and out.data_source == "live_mcp"
    assert out.skill_version and len(out.skill_hash) == 64
    assert all(p.total_fen == 800 and p.items[0].line_total_fen == 1000 for p in out.plans)
    assert all(p.quote_id == "official-quote" and p.items[0].line_price_basis == "menu_reference" for p in out.plans)
    assert len({p.plan_id for p in out.plans}) == 2
    assert svc.store.get(out.plans[0].plan_id)[0].quote_id == "official-quote"


def test_skill_loaded_into_strict_responses_request_not_just_metadata():
    model = Model({"candidates": [candidate()]})
    planner = make_planner(model)
    plans = run(planner.generate([burger()], Requirements(people=1, budget_fen=5000, appetite="heavy"), context()))
    assert len(plans) == 1 and plans[0].side_servings_total == 0
    payload, timeout = model.calls[0]
    assert timeout == 60
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["additionalProperties"] is False
    assert "个人甜品" in payload["instructions"] and "不是指令" in payload["instructions"]
    assert json.loads(payload["input"])["items"][0]["code"] == "burger"


def test_legal_non_default_option_not_blocked_by_unknown_default():
    item, detail = combo()
    model = Model({"candidates": [candidate("combo", selected=selected("fish"))]})
    plans = run(make_planner(model).generate([item], Requirements(people=1, budget_fen=5000), context({"combo": detail})))
    assert plans[0].items[0].unit_price_fen == 1200
    assert plans[0].items[0].selected_options[0].option_name == "麦香鱼"
    assert plans[0].items[0].servings == 1


def test_same_product_with_distinct_options_stays_two_lines_identical_merges():
    item, detail = combo()
    detail.rounds[0].options[0].diff_price_fen = 0
    basket = candidate("combo", selected=selected("fish"))
    basket["items"] += [candidate("combo", selected=selected("burger"))["items"][0],
                        candidate("combo", selected=selected("fish"))["items"][0]]
    plans = run(make_planner(Model({"candidates": [basket]})).generate(
        [item], Requirements(people=3, budget_fen=5000), context({"combo": detail})))
    assert len(plans[0].items) == 2
    assert [(l.selected_options[0].option_code, l.quantity) for l in plans[0].items] == [("fish", 2), ("burger", 1)]
    assert plans[0].total_fen == 3400


@pytest.mark.parametrize("bad", ["fake", "price", "missing", "unknown_diff", "unknown_bound", "membership", "quantity"])
def test_fact_errors_get_only_one_correction_then_reject(bad):
    item, detail = combo()
    value = candidate("combo", selected=selected("fish"))
    if bad == "fake": value["items"][0]["code"] = "invented"
    if bad == "price": value["items"][0]["total_fen"] = 1
    if bad == "missing": value["items"][0]["selected_options"] = []
    if bad == "unknown_diff": detail.rounds[0].options[1].diff_price_fen = None
    if bad == "unknown_bound": detail.rounds[0].min_quantity = None
    if bad == "membership": item.requires_membership = True
    if bad == "quantity": value["items"][0]["quantity"] = True
    model = Model({"candidates": [value]}, {"candidates": [value]})
    with pytest.raises(planner_module().PlanningError) as error:
        run(make_planner(model).generate([item], Requirements(people=1, budget_fen=5000), context({"combo": detail})))
    assert error.value.code == "E_CANDIDATE_INVALID"
    assert len(model.calls) == 2 and "validation_errors" in json.loads(model.calls[1][0]["input"])


def test_correction_can_replace_unknown_product_with_real_product():
    model = Model({"candidates": [candidate("fake")]}, {"candidates": [candidate()]})
    plans = run(make_planner(model).generate([burger()], Requirements(people=1, budget_fen=5000), context()))
    assert plans[0].items[0].code == "burger" and len(model.calls) == 2


def test_transport_failure_no_retry_no_fallback(tmp_path):
    model = Model(RuntimeError("private upstream error"))
    svc = service(tmp_path, model)
    with pytest.raises(planner_module().PlanningError) as error:
        run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=5000))))
    assert error.value.code == "E_LLM_REQUEST_FAILED" and len(model.calls) == 1
    assert not svc.store._records
    assert "private upstream" not in error.value.message


def test_no_candidates_is_not_a_mathematical_unsatisfiability_claim(tmp_path):
    out = run(service(tmp_path, Model({"candidates": []})).create_plans(
        PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=0))))
    assert out.plans == [] and out.unsatisfiable_reason
    assert "无解" not in out.unsatisfiable_reason


def test_required_unselected_modification_keys_are_not_silently_dropped():
    raw = {"categories": [{"name": "汉堡", "meals": [{"code": "x"}]}],
           "meals": {"x": {"name": "双层吉士汉堡", "currentPrice": "10", "originalPrice": "10",
                           "modification": {"values": [{"code": "sauce", "selectedKey": "yes", "unselectedKey": "no"}]}}}}
    items, _ = map_menu("store", raw)
    assert not items or items[0].available is False


@pytest.mark.parametrize("mode", ["demo", "auto", "live"])
def test_menu_modes_never_call_snapshot_or_return_it(mode, monkeypatch):
    from backend.config import settings
    from backend.providers import McpError
    from backend.services.menu_service import MenuService
    monkeypatch.setattr(settings, "data_mode", mode)
    svc = MenuService()
    async def fail(*args): raise McpError("offline")
    async def forbidden(*args): pytest.fail("snapshot fallback")
    svc._fetch_menu_raw = fail
    svc._snapshot = SimpleNamespace(get_menu=forbidden)
    with pytest.raises(McpError, match="offline"):
        run(svc.get_menu("store"))

def test_chat_failure_does_not_replace_model_with_rules(monkeypatch):
    from backend.services.chat_service import ChatService
    from backend.schemas import ChatIn
    from backend import llm
    async def failed(*args, **kwargs):
        raise llm.ExtractionError("fixture-model-failed")
    monkeypatch.setattr(llm, "extract_requirements", failed)
    with pytest.raises(llm.ExtractionError, match="fixture-model-failed"):
        run(ChatService().parse(ChatIn(message="2人总预算100元")))


def test_chat_uses_online_responses_even_with_old_demo_flag(monkeypatch):
    from backend import llm
    calls = []
    def response(payload, timeout):
        calls.append(payload)
        return {"updates": {}, "evidence": {}}
    monkeypatch.setattr(llm, "_responses_request", response)
    out = run(llm.extract_requirements("2人总预算100元", demo_mode=True))
    assert out["parse_source"] == "llm"
    assert calls and calls[0]["text"]["format"]["strict"] is True
    assert calls[0]["model"]


@pytest.mark.parametrize("service_name", ["combos", "nutrition"])
def test_optional_data_failure_never_uses_snapshots(service_name, monkeypatch):
    from backend.providers import McpError
    from backend.services.combo_service import ComboService
    from backend.services.nutrition_service import NutritionService
    async def fail(*args): raise McpError("offline")
    async def forbidden(*args): pytest.fail("optional snapshot fallback")
    if service_name == "combos":
        svc = ComboService(mcp=SimpleNamespace(get_meal_detail=fail))
        svc._snapshot = SimpleNamespace(get_meal_detail=forbidden)
        result = run(svc.get_details("store", ["combo"]))
        assert result.details == {} and result.missing_codes == ["combo"]
    else:
        svc = NutritionService()
        monkeypatch.setattr(svc.menus, "_fetch_nutrition_raw", fail)
        svc._snapshot = SimpleNamespace(get_nutrition=forbidden)
        with pytest.raises(McpError): run(svc.get_nutrition_map())

def test_malformed_model_json_can_be_corrected_once():
    model = Model("not valid JSON", {"candidates": [candidate()]})
    plans = run(make_planner(model).generate([burger()], Requirements(people=1, budget_fen=5000), context()))
    assert len(plans) == 1 and len(model.calls) == 2


def test_over_budget_quote_drops_only_that_candidate(tmp_path):
    from backend.services.checkout_service import CheckoutError
    class BudgetQuote(Quote):
        async def quote(self, plan, store_code, requirements):
            if plan.items[0].code == "a":
                raise CheckoutError("E_QUOTE_OVER_BUDGET", "over budget")
            return await super().quote(plan, store_code, requirements)
    svc = service(tmp_path, Model({"candidates": [candidate("a"), candidate("b")]}),
                  [burger("a"), burger("b")], BudgetQuote())
    out = run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=1000))))
    assert len(out.plans) == 1 and out.plans[0].items[0].code == "b"
    assert len(svc.store._records) == 1


def test_top_level_required_modification_is_excluded_not_optional_support_modify():
    item, detail = combo()
    raw = {"code": "combo", "name": "随心配", "supportModify": True,
           "modification": {"values": [{"code": "sauce", "unselectedKey": "no"}]},
           "rounds": [{"id": "r", "minQuantity": 1, "maxQuantity": 1,
                       "choices": [{"code": "burger", "name": "双层吉士汉堡", "diffPrice": "0"}]}]}
    assert not map_meal_detail(raw, "live_mcp").rounds
    del raw["modification"]
    assert map_meal_detail(raw, "live_mcp").rounds

def test_responses_transport_passes_invalid_json_to_bounded_correction(monkeypatch):
    import io
    from backend import llm
    monkeypatch.setattr(llm._configuration.settings, "llm_provider", "api")
    monkeypatch.setattr(llm._configuration.settings, "deepseek_api_key", "fixture-only")
    body = {"choices": [{"message": {"content": "invalid json"}, "finish_reason": "stop"}]}
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *args, **kwargs: Response(json.dumps(body).encode()))
    assert llm._responses_request({"model": "fixture"}, 60) == "invalid json"


def test_valid_six_person_candidate_survives_invalid_basket_and_schema_neighbor():
    malformed = candidate(quantity=6)
    malformed["items"][0]["quantity"] = "6"
    model = Model({"candidates": [candidate(quantity=1), candidate(quantity=6, title="六人汉堡"), malformed]})
    plans = run(make_planner(model).generate(
        [burger()], Requirements(people=6, budget_fen=15000), context()))
    assert [(p.title, p.servings_total, p.type) for p in plans] == [("六人汉堡", 6, "recommended")]
    assert len(model.calls) == 1


def test_duplicate_valid_candidates_keep_first_basket_in_model_order():
    model = Model({"candidates": [candidate("a", title="先选"), candidate("a", title="重复"),
                                  candidate("b", title="后选")]})
    plans = run(make_planner(model).generate(
        [burger("a"), burger("b")], Requirements(people=1, budget_fen=5000), context()))
    assert [(p.title, p.items[0].code, p.type) for p in plans] == [
        ("先选", "a", "recommended"), ("后选", "b", "alternative")]
    assert len(model.calls) == 1


def test_shortage_repair_receives_previous_candidates_and_verified_line_contributions():
    first = candidate(quantity=1)
    model = Model({"candidates": [first]}, {"candidates": [candidate(quantity=6)]})
    plans = run(make_planner(model).generate(
        [burger()], Requirements(people=6, budget_fen=15000), context()))
    repair = json.loads(model.calls[1][0]["input"])
    assert repair["previous_candidates"] == [first]
    failure = repair["validation_errors"][0]
    assert failure["candidate_index"] == 0
    assert failure["required_main_servings"] == 6
    assert failure["main_servings_total"] == 1
    assert failure["non_spicy_servings_total"] == 1
    assert failure["line_facts"][0]["main_servings_per_unit"] == 1
    assert failure["line_facts"][0]["main_servings"] == 1
    assert failure["line_facts"][0]["non_spicy_servings"] == 1
    assert plans[0].servings_total == 6


def test_combo_repair_never_multiplies_per_combo_selection_by_basket_quantity():
    item, detail = combo()
    wrong = candidate("combo", quantity=6,
                      selected=[{"round_id": "main", "option_code": "fish", "quantity": 6}])
    correct = candidate("combo", quantity=6, selected=selected("fish"))
    model = Model({"candidates": [wrong]}, {"candidates": [correct]})
    plans = run(make_planner(model).generate(
        [item], Requirements(people=6, budget_fen=15000), context({"combo": detail})))
    repair = json.loads(model.calls[1][0]["input"])
    assert repair["previous_candidates"] == [wrong]
    line = repair["validation_errors"][0]["line_facts"][0]
    assert line["code"] == "combo" and line["quantity"] == 6
    assert any("1/1" in error for error in line["issues"])
    assert line["main_servings"] is None
    assert plans[0].servings_total == 6
    assert plans[0].items[0].selected_options[0].quantity == 1


def test_model_facts_exclude_unselectable_items_not_legal_non_default_options():
    item, detail = combo()
    item.servings = detail.servings = 6  # A marketing name does not prove six main dishes.
    unavailable = burger("unavailable")
    unavailable.available = False
    member = burger("member")
    member.requires_membership = True
    missing = item.model_copy(update={"code": "missing"})
    unverified = burger("unverified")
    unverified.price_verified = False
    merch = burger("merch")
    merch.is_merchandise = True
    model = Model({"candidates": [candidate("combo", selected=selected("fish"))]})
    run(make_planner(model).generate(
        [unavailable, member, missing, unverified, merch, burger(), item],
        Requirements(people=1, budget_fen=5000), context({"combo": detail})))
    data = json.loads(model.calls[0][0]["input"])
    assert [i["code"] for i in data["items"]] == ["burger", "combo"]
    assert data["items"][0]["main_servings_per_unit"] == 1
    assert data["items"][0]["non_spicy_servings_per_unit"] == 1
    assert data["items"][1]["main_servings_per_unit"] is None
    assert "servings" not in data["items"][1]
    projected = data["context"]["combo_details"]["combo"]
    assert "servings" not in projected
    assert [(o["code"], o["main_servings_per_unit"]) for o in projected["rounds"][0]["options"]] == [("fish", 1)]
    assert detail.rounds[0].options[0].diff_price_fen is None
    assert item.servings == 6 and len(detail.rounds[0].options) == 2


def test_model_option_facts_never_count_side_or_drink_as_main():
    item, detail = combo()
    detail.rounds[0].options += [
        detail.rounds[0].options[1].model_copy(update={
            "code": "side", "name": "六块麦乐鸡", "food_role": "side", "diff_price_fen": 0}),
        detail.rounds[0].options[1].model_copy(update={
            "code": "drink", "name": "可乐", "food_role": "beverage", "diff_price_fen": 0}),
    ]
    model = Model({"candidates": [candidate("combo", selected=selected("fish"))]})
    run(make_planner(model).generate(
        [item], Requirements(people=1, budget_fen=5000), context({"combo": detail})))
    options = json.loads(model.calls[0][0]["input"])["context"]["combo_details"]["combo"]["rounds"][0]["options"]
    assert {o["code"]: o["main_servings_per_unit"] for o in options} == {"fish": 1, "side": 0, "drink": 0}


def test_all_invalid_candidates_retry_once_without_blame_to_user_requirements():
    model = Model({"candidates": [candidate(quantity=1)]}, {"candidates": [candidate(quantity=1)]})
    with pytest.raises(planner_module().PlanningError) as caught:
        run(make_planner(model).generate([burger()], Requirements(people=6, budget_fen=15000), context()))
    assert caught.value.code == "E_CANDIDATE_INVALID"
    assert caught.value.message == "这次没生成有效方案，请重试。"
    assert len(model.calls) == 2


@pytest.mark.parametrize("budget_type,budget_fen,want_total", [("total", 15000, 15000),
                                                            ("per_person", 2500, 15000)])
def test_model_receives_explicit_total_budget_and_reference_price_quantity_units(budget_type, budget_fen, want_total):
    model = Model({"candidates": [candidate(quantity=6)]})
    run(make_planner(model).generate([burger()], Requirements(
        people=6, budget_type=budget_type, budget_fen=budget_fen), context()))
    data = json.loads(model.calls[0][0]["input"])
    assert data["total_budget_fen"] == want_total
    assert data["pricing_context"] == {
        "currency": "CNY", "amount_unit": "fen", "reference_price_basis": "menu_reference",
        "line_quantity_basis": "purchased_units", "selected_option_quantity_basis": "per_combo_unit",
        "official_quote_required": True, "reference_over_budget_proves_unsatisfiable": False,
    }
    assert data["items"][0]["reference_base_price_fen"] == 1000


def test_shortage_repair_prices_combo_surcharge_per_unit_before_line_quantity():
    item, detail = combo()
    first = candidate("combo", quantity=3, selected=selected("fish"))
    model = Model({"candidates": [first]}, {
        "candidates": [candidate("combo", quantity=6, selected=selected("fish"))]})
    plans = run(make_planner(model).generate([item], Requirements(
        people=6, budget_fen=15000), context({"combo": detail})))
    data = json.loads(model.calls[1][0]["input"])
    failure = data["validation_errors"][0]
    line = failure["line_facts"][0]
    assert data["items"][0]["reference_base_price_fen"] == 1000
    assert line["reference_unit_price_fen"] == 1200
    assert line["reference_line_total_fen"] == 3600
    assert failure["reference_total_fen"] == 3600
    assert failure["total_budget_fen"] == 15000
    assert plans[0].total_fen == 7200


def test_invalid_option_reference_total_is_unknown_not_false_zero_price():
    item, detail = combo()
    wrong = candidate("combo", quantity=6, selected=selected("burger"))  # Its surcharge is unknown.
    model = Model({"candidates": [wrong]}, {
        "candidates": [candidate("combo", quantity=6, selected=selected("fish"))]})
    run(make_planner(model).generate([item], Requirements(
        people=6, budget_fen=15000), context({"combo": detail})))
    failure = json.loads(model.calls[1][0]["input"])["validation_errors"][0]
    assert failure["line_facts"][0]["reference_unit_price_fen"] is None
    assert failure["line_facts"][0]["reference_line_total_fen"] is None
    assert failure["reference_total_fen"] is None


def test_reference_over_budget_candidate_can_still_pass_cheaper_official_quote(tmp_path):
    model = Model({"candidates": [candidate(quantity=6)]})
    svc = service(tmp_path, model, [burger(price=3000)])
    out = run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(
        people=6, budget_fen=15000))))
    assert len(out.plans) == 1
    assert out.plans[0].items[0].line_total_fen == 18000
    assert out.plans[0].total_fen == 800 and out.plans[0].price_basis == "live_quote"
    assert len(model.calls) == 1


class OfficialBasketQuote(Quote):
    """External price boundary; exercise the real model rebuild and orchestrator."""
    def __init__(self, prices):
        self.prices, self.calls = prices, []

    async def quote(self, plan, store_code, requirements):
        from backend.services.checkout_service import CheckoutError
        code = plan.items[0].code
        self.calls.append(code)
        total = self.prices[code]
        if isinstance(total, Exception):
            raise total
        if total > requirements.total_budget_fen:
            raise CheckoutError("E_QUOTE_OVER_BUDGET", "over budget", {
                "total_fen": total, "budget_fen": requirements.total_budget_fen,
                "over_by_fen": total - requirements.total_budget_fen})
        quoted = await super().quote(plan.model_copy(deep=True), store_code, requirements)
        quoted.total_fen = total
        return quoted


def test_all_over_budget_official_quotes_return_facts_to_same_model_and_reselect(tmp_path):
    model = Model({"candidates": [candidate("a", quantity=6)]},
                  {"candidates": [candidate("b", quantity=6)]})
    quotes = OfficialBasketQuote({"a": 18000, "b": 12000})
    svc = service(tmp_path, model, [burger("a"), burger("b")], quotes)
    out = run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(
        people=6, budget_fen=15000))))
    assert [(p.items[0].code, p.total_fen, p.type) for p in out.plans] == [("b", 12000, "recommended")]
    assert quotes.calls == ["a", "b"] and len(model.calls) == 2
    repair = json.loads(model.calls[1][0]["input"])
    feedback = repair["quote_feedback"][0]
    assert (feedback["total_fen"], feedback["budget_fen"], feedback["over_by_fen"]) == (18000, 15000, 3000)
    assert feedback["candidate"]["items"] == [{"code": "a", "quantity": 6, "selected_options": []}]
    assert feedback["reference_total_fen"] == 6000
    assert len(svc.store._records) == 1


def test_second_over_budget_round_stops_without_claiming_global_unsatisfiability(tmp_path):
    model = Model({"candidates": [candidate("a")]}, {"candidates": [candidate("b")]})
    quotes = OfficialBasketQuote({"a": 2000, "b": 2100})
    svc = service(tmp_path, model, [burger("a"), burger("b")], quotes)
    out = run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=1000))))
    assert out.plans == [] and len(model.calls) == 2 and quotes.calls == ["a", "b"]
    assert out.unsatisfiable_reason and "无解" not in out.unsatisfiable_reason
    assert not svc.store._records


def test_partially_valid_quotes_keep_model_order_without_extra_model_call(tmp_path):
    model = Model({"candidates": [candidate("a"), candidate("b"), candidate("c")]})
    quotes = OfficialBasketQuote({"a": 2000, "b": 900, "c": 700})
    svc = service(tmp_path, model, [burger("a"), burger("b"), burger("c")], quotes)
    out = run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=1000))))
    assert [(p.items[0].code, p.total_fen, p.type) for p in out.plans] == [
        ("b", 900, "recommended"), ("c", 700, "alternative")]
    assert len(model.calls) == 1 and len(svc.store._records) == 2


def test_repeat_rejected_basket_is_not_quoted_again_and_new_basket_can_pass(tmp_path):
    model = Model({"candidates": [candidate("a")]},
                  {"candidates": [candidate("a", title="换个名字"), candidate("b")]})
    quotes = OfficialBasketQuote({"a": 2000, "b": 900})
    svc = service(tmp_path, model, [burger("a"), burger("b")], quotes)
    out = run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=1000))))
    assert quotes.calls == ["a", "b"] and len(model.calls) == 2
    assert [(p.items[0].code, p.type) for p in out.plans] == [("b", "recommended")]


def test_quote_feedback_does_not_invent_missing_official_total(tmp_path):
    from backend.services.checkout_service import CheckoutError
    model = Model({"candidates": [candidate("a")]}, {"candidates": [candidate("b")]})
    quotes = OfficialBasketQuote({"a": CheckoutError("E_QUOTE_OVER_BUDGET", "over budget"), "b": 900})
    svc = service(tmp_path, model, [burger("a"), burger("b")], quotes)
    out = run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=1000))))
    assert out.plans[0].items[0].code == "b"
    feedback = json.loads(model.calls[1][0]["input"])["quote_feedback"][0]
    assert feedback["total_fen"] is None and feedback["over_by_fen"] is None
    assert feedback["budget_fen"] == 1000


def test_later_quote_failure_does_not_discard_already_valid_basket(tmp_path):
    from backend.services.checkout_service import CheckoutError
    model = Model({"candidates": [candidate("a"), candidate("b")]})
    quotes = OfficialBasketQuote({"a": 900, "b": CheckoutError("E_QUOTE_FAILED", "unavailable")})
    svc = service(tmp_path, model, [burger("a"), burger("b")], quotes)
    out = run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=1000))))
    assert [(p.items[0].code, p.total_fen) for p in out.plans] == [("a", 900)]
    assert len(model.calls) == 1 and len(svc.store._records) == 1


def test_planning_deadline_covers_slow_model_without_long_client_hang(monkeypatch, tmp_path):
    module = importlib.import_module("backend.services.plan_service")
    monkeypatch.setattr(module, "PLANNING_TIMEOUT_SECONDS", 0.02, raising=False)
    class SlowModel:
        async def __call__(self, payload, timeout):
            await asyncio.sleep(0.1)
            return {"candidates": [candidate()]}
    svc = service(tmp_path, SlowModel())
    with pytest.raises(planner_module().PlanningError) as caught:
        run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=1000))))
    assert caught.value.code == "E_PLANNING_TIMEOUT" and not svc.store._records


def test_deadline_during_later_quote_returns_already_quoted_basket(monkeypatch, tmp_path):
    module = importlib.import_module("backend.services.plan_service")
    monkeypatch.setattr(module, "PLANNING_TIMEOUT_SECONDS", 0.02, raising=False)
    class SlowSecondQuote(OfficialBasketQuote):
        async def quote(self, plan, store_code, requirements):
            if plan.items[0].code == "b":
                await asyncio.sleep(0.1)
            return await super().quote(plan, store_code, requirements)
    model = Model({"candidates": [candidate("a"), candidate("b")]})
    svc = service(tmp_path, model, [burger("a"), burger("b")], SlowSecondQuote({"a": 900, "b": 800}))
    out = run(svc.create_plans(PlanRequest(store_code="store", requirements=Requirements(people=1, budget_fen=1000))))
    assert [(p.items[0].code, p.total_fen) for p in out.plans] == [("a", 900)]
    assert len(svc.store._records) == 1
