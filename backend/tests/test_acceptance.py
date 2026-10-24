# 修订说明：修正随心配无饮料的旧断言，隔离缓存修改，避免验收用例互相污染。
"""验收场景与边界测试（demo 快照模式，不依赖外部凭据）。

运行（仓库根目录）：
    DATA_MODE=demo ./.venv/Scripts/python.exe -m pytest backend/tests -v   # Windows git bash

测试数据来自真实 MCP 采集的快照 backend/data/snapshot_1400011.json。
这些测试验证的是接口与约束行为，不是「算法最优性」（那是 B 的正式算法的验收范围）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DATA_MODE"] = "demo"

from backend.schemas import DietaryConstraint, PlanRequest, Requirements  # noqa: E402
from backend.services.chat_service import rule_extract  # noqa: E402
from backend.services.menu_service import MenuService  # noqa: E402
from backend.services.plan_service import PlanService  # noqa: E402

STORE = "1400011"


@pytest.fixture(scope="module")
def services(tmp_path_factory):
    from backend.services.plan_store import PlanStore

    menu_service = MenuService()
    store_path = tmp_path_factory.mktemp("plans") / "plans.json"
    plan_service = PlanService(menus=menu_service, store=PlanStore(store_path))
    yield menu_service, plan_service
    asyncio.run(menu_service.aclose())


def run(coro):
    return asyncio.run(coro)


def make_req(people, budget_fen, budget_type="total", non_spicy=0):
    dietary = []
    if non_spicy:
        dietary = [DietaryConstraint(kind="non_spicy", people=non_spicy)]
    return Requirements(
        people=people,
        budget_type=budget_type,
        budget_fen=budget_fen,
        dietary_constraints=dietary,
    )


# ------------------------------------------------------------ 门店与菜单


def test_stores_demo_source(services):
    out = run(services[0].get_stores())
    assert out.data_source == "demo_snapshot"
    assert len(out.stores) > 0
    assert out.stores[0].store_code
    assert out.fetched_at


def test_menu_items_have_spicy_and_membership_flags(services):
    menu = run(services[0].get_menu(STORE))
    assert menu.data_source == "demo_snapshot"
    assert len(menu.items) >= 50
    membership = [i for i in menu.items if i.requires_membership]
    assert len(membership) > 0
    for item in membership:
        assert item.membership_note
    assert all(i.spicy_level in ("spicy", "mild", "unknown") for i in menu.items)


# ------------------------------------------------------------ 需求解析


def test_chat_full_sentence():
    r = rule_extract("我们6个人，总预算150元，其中2个人不吃辣", None)
    assert r["requirements"].people == 6
    assert r["requirements"].budget_type == "total"
    assert r["requirements"].budget_fen == 15000
    assert r["requirements"].dietary_constraints[0].people == 2
    assert r["missing_fields"] == []


def test_chat_per_person():
    r = rule_extract("4个人，人均30元", None)
    assert r["requirements"].budget_type == "per_person"
    assert r["requirements"].budget_fen == 3000


def test_chat_missing_budget_asks():
    r = rule_extract("我们三个人", None)
    assert r["missing_fields"] == ["budget"]
    assert "预算" in r["follow_up"]


def test_chat_spicy_without_count_asks():
    r = rule_extract("有人不吃辣", None)
    assert "non_spicy_people" in r["missing_fields"]
    assert "不吃辣" in r["follow_up"]


# ------------------------------------------------------------ 方案生成


def test_plans_competition_scenario(services):
    """赛事原题：6 人 / 150 元 / 2 人不吃辣。"""
    req = make_req(6, 15000, non_spicy=2)
    out = run(services[1].create_plans(PlanRequest(store_code=STORE, requirements=req)))
    assert out.data_source == "demo_snapshot"
    assert out.unsatisfiable_reason is None
    assert len(out.plans) >= 1
    for plan in out.plans:
        checks = {c.name: c.satisfied for c in plan.checks}
        assert checks["预算"] and checks["人数"] and checks["忌口"]
        assert plan.total_fen <= 15000
        assert plan.servings_total >= 6
        assert plan.non_spicy_servings >= 2
        # 不允许用随单购麦金卡的低价凑数
        menu_codes = {i.code: i for i in run(services[0].get_menu(STORE)).items}
        for item in plan.items:
            assert not menu_codes[item.code].requires_membership


def test_plans_per_person_budget_conversion(services):
    req = make_req(4, 3000, budget_type="per_person")
    out = run(services[1].create_plans(PlanRequest(store_code=STORE, requirements=req)))
    assert out.total_budget_fen == 12000
    for plan in out.plans:
        assert plan.total_fen <= 12000
        assert plan.servings_total >= 4


def test_plans_unsatisfiable_reports_reason(services):
    req = make_req(6, 3000, non_spicy=2)
    out = run(services[1].create_plans(PlanRequest(store_code=STORE, requirements=req)))
    assert out.plans == []
    assert out.unsatisfiable_reason
    assert "预算" in out.unsatisfiable_reason


def test_plans_zero_budget_unsatisfiable(services):
    req = make_req(1, 0)
    out = run(services[1].create_plans(PlanRequest(store_code=STORE, requirements=req)))
    assert out.plans == []
    assert out.unsatisfiable_reason


def test_plans_no_duplicate_plan_ids(services):
    req = make_req(6, 15000, non_spicy=2)
    out = run(services[1].create_plans(PlanRequest(store_code=STORE, requirements=req)))
    ids = [p.plan_id for p in out.plans]
    assert len(ids) == len(set(ids))


def test_plans_max_people_boundary(services):
    req = make_req(20, 50000)
    out = run(services[1].create_plans(PlanRequest(store_code=STORE, requirements=req)))
    for plan in out.plans:
        assert plan.servings_total >= 20


def test_non_spicy_people_capped_at_total(services):
    """忌口人数超过总人数时应收敛到总人数，不能生成超过人数的份数需求。"""
    req = make_req(6, 15000, non_spicy=10)
    assert req.non_spicy_people == 6
    out = run(services[1].create_plans(PlanRequest(store_code=STORE, requirements=req)))
    for plan in out.plans:
        assert plan.servings_total >= 6


# ------------------------------------------------------------ 方案存取与模拟结算


def test_store_switch_invalidates_old_plans(services):
    plan_service = services[1]
    req = make_req(2, 10000)
    out1 = run(plan_service.create_plans(PlanRequest(store_code=STORE, requirements=req)))
    old_id = out1.plans[0].plan_id
    # 换门店重新生成
    run(plan_service.create_plans(PlanRequest(store_code="1400401", requirements=req)))
    with pytest.raises(Exception):
        plan_service.store.get(old_id)


def test_order_rebuilds_amount_from_stored_plan(services):
    plan_service = services[1]
    req = make_req(2, 10000)
    out = run(plan_service.create_plans(PlanRequest(store_code=STORE, requirements=req)))
    plan_id = out.plans[0].plan_id
    plan, store_code, _ = plan_service.store.get(plan_id)
    assert store_code == STORE
    assert plan.total_fen == sum(i.line_total_fen for i in plan.items)
    assert plan.discount_fen == plan.subtotal_fen - plan.total_fen


# ------------------------------------------------------------ 失败处理（§7）


def test_upstream_timeout_invalidates_old_plans(services, monkeypatch):
    """§7：MCP 读取超时 → 给出明确错误，且旧方案失效。"""
    from backend.providers import McpTimeout

    menu_service, plan_service = services
    req = make_req(2, 10000)
    out = run(plan_service.create_plans(PlanRequest(store_code=STORE, requirements=req)))
    old_id = out.plans[0].plan_id

    async def boom(store_code):
        raise McpTimeout("模拟上游超时")

    monkeypatch.setattr(menu_service, "get_menu", boom)
    with pytest.raises(McpTimeout):
        run(plan_service.create_plans(PlanRequest(store_code=STORE, requirements=req)))
    with pytest.raises(Exception):
        plan_service.store.get(old_id)


def test_degraded_snapshot_invalidates_old_plans(services, monkeypatch):
    """auto 模式降级到快照后，旧方案价格不再可信，必须失效。"""
    menu_service, plan_service = services
    req = make_req(2, 10000)
    out = run(plan_service.create_plans(PlanRequest(store_code=STORE, requirements=req)))
    old_id = out.plans[0].plan_id

    original = menu_service.get_menu

    async def degraded(store_code):
        menu = await original(store_code)
        menu.degraded_reason = "模拟降级"
        return menu

    monkeypatch.setattr(menu_service, "get_menu", degraded)
    run(plan_service.create_plans(PlanRequest(store_code=STORE, requirements=req)))
    with pytest.raises(Exception):
        plan_service.store.get(old_id)


def test_plan_expires_after_ttl(services):
    plan_service = services[1]
    req = make_req(2, 10000)
    out = run(plan_service.create_plans(PlanRequest(store_code=STORE, requirements=req)))
    plan_id = out.plans[0].plan_id
    record = plan_service.store._records[plan_id]  # noqa: SLF001
    record.created_at -= 31 * 60  # 模拟超过 30 分钟
    with pytest.raises(Exception):
        plan_service.store.get(plan_id)


# ------------------------------------------------------------ V1.1 多轮草稿


def test_draft_multi_turn_keeps_earlier_fields():
    """先说人数、后说预算时，已收集的信息不能丢。"""
    draft = None
    for message in ["我们6个人", "预算150元", "其中2个人不吃辣"]:
        result = rule_extract(message, draft)
        draft = result["draft"]
    assert draft.people == 6, "忌口句式里的数字不能被当成总人数"
    assert draft.budget_fen == 15000
    assert draft.dietary_constraints[0].people == 2
    assert result["requirements"] is not None


def test_draft_correction_sentence():
    """「不是 6 个人，是 4 个人」应取更正后的值。"""
    draft = rule_extract("我们6个人，预算150元", None)["draft"]
    result = rule_extract("不是6个人，是4个人", draft)
    assert result["draft"].people == 4
    assert result["draft"].budget_fen == 15000  # 其他已确认信息保留


def test_draft_contradiction_asks_instead_of_silently_capping():
    """不吃辣人数超过总人数时要追问，不能用 min 静默收敛。"""
    draft = rule_extract("6个人，预算150元", None)["draft"]
    result = rule_extract("10个人不吃辣", draft)
    assert result["requirements"] is None
    assert "contradiction" in result["missing_fields"]
    assert "超过" in result["follow_up"]


def test_draft_exclude_beverage_and_membership():
    draft = rule_extract("6个人，预算150元，不要饮料，我有麦金卡", None)["draft"]
    assert draft.exclude_beverage is True
    assert draft.membership_status == "active"


# ------------------------------------------------------------ V1.1 不带饮料硬约束


def test_plans_exclude_beverage(services):
    """不带饮料：方案里不能出现任何未证实不含饮品的商品。"""
    req = Requirements(people=6, budget_type="total", budget_fen=15000, exclude_beverage=True)
    out = run(services[1].create_plans(PlanRequest(store_code=STORE, requirements=req)))
    assert out.plans, "不带饮料在演示快照下应能出方案"
    menu = {i.code: i for i in run(services[0].get_menu(STORE)).items}
    for plan in out.plans:
        checks = {c.name: c.satisfied for c in plan.checks}
        assert checks["不含饮料"] is True
        for item in plan.items:
            # A 的无饮料样例只使用已确认单品；套餐替换由 B 显式选配后再校验。
            assert menu[item.code].beverage_status == "none"
            assert not menu[item.code].is_combo
            assert not menu[item.code].is_merchandise


def test_mixed_round_names_do_not_prove_beverage_free(services):
    # 回归：轮次叫蓝区/粉区的套餐，默认选项实际仍有可乐或怡泉。
    """不能把没有名为饮料的轮次误当作不含饮料。"""
    from backend.services.combo_service import ComboService

    menu = run(services[0].get_menu(STORE))
    combos = ComboService()
    codes = [i.code for i in menu.items if i.is_combo and not i.is_merchandise]
    details = run(combos.get_details(STORE, codes)).details
    for code in ("9900013304", "9900015568"):
        assert details[code].beverage_status == "contains"
        assert details[code].servings_verified is False
    mandatory = {c for c, d in details.items() if d.has_mandatory_beverage}
    # 饮料必选的套餐绝不能出现在不带饮料方案里
    req = Requirements(people=6, budget_type="total", budget_fen=15000, exclude_beverage=True)
    out = run(services[1].create_plans(PlanRequest(store_code=STORE, requirements=req)))
    for plan in out.plans:
        for item in plan.items:
            assert item.code not in mandatory


def test_hard_constraint_unavailable_is_rejected(services):
    """取不到套餐结构、也没有可确认的主食单品时，必须明确拒绝而不是硬凑。"""
    from backend.services.capabilities import CapabilityUnavailable

    menu_service, plan_service = services
    req = Requirements(people=4, budget_type="total", budget_fen=15000, exclude_beverage=True)

    original = menu_service.get_menu

    async def no_main_courses(store_code):
        # 只污染本次模拟响应，不修改服务缓存，避免后续验收继承未知角色。
        menu = (await original(store_code)).model_copy(deep=True)
        # 把所有主食单品的角色改成未知，模拟「无法确认是否含饮料」
        for item in menu.items:
            if item.food_role == "main":
                item.food_role = "unknown"
                item.beverage_status = "unknown"
        return menu

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(menu_service, "get_menu", no_main_courses)
    try:
        with pytest.raises(CapabilityUnavailable) as exc:
            run(plan_service.create_plans(PlanRequest(store_code=STORE, requirements=req)))
        assert exc.value.capability == "exclude_beverage"
    finally:
        monkeypatch.undo()


# ------------------------------------------------------------ V1.1 套餐结构与能力标记


def test_combo_details_beverage_round_is_mandatory(services):
    """实测：套餐的饮料轮 minQuantity=1，因此去不掉。"""
    from backend.services.combo_service import ComboService

    combos = ComboService()
    detail = run(combos.get_details(STORE, ["9900015602"])).details.get("9900015602")
    assert detail is not None, "演示快照应含套餐结构"
    beverage_rounds = [r for r in detail.rounds if r.is_beverage_round]
    assert beverage_rounds, "三件套应有饮料轮"
    assert beverage_rounds[0].min_quantity == 1
    assert beverage_rounds[0].required is True
    assert detail.has_mandatory_beverage is True
    # 差价必须是整数分；解析不出来时是 None，不能是 0
    for r in detail.rounds:
        for opt in r.options:
            assert opt.diff_price_fen is None or isinstance(opt.diff_price_fen, int)


def test_capabilities_reflect_full_chain(services):
    from backend.services.capabilities import static_capabilities

    caps = static_capabilities(skill_module=None)
    assert caps["chat_draft"] is True
    assert caps["exclude_beverage"] is True  # 无 B 模块时走 A 的样例
    # 字段存在不等于决策生效
    assert caps["appetite_planning"] is False
    assert caps["taste_matching"] is False
    assert caps["membership_comparison"] is False
    assert caps["campaigns"] is False

    out = run(
        services[1].create_plans(
            PlanRequest(store_code=STORE, requirements=make_req(2, 10000))
        )
    )
    assert out.capabilities["chat_draft"] is True


def test_eligibility_restricted_items_excluded(services):
    """随单购麦金卡与学生专享的低价都不能直接进方案。"""
    menu = run(services[0].get_menu(STORE))
    restricted = [i for i in menu.items if i.requires_membership]
    assert restricted, "快照里应存在资格受限商品"
    for item in restricted:
        assert item.membership_note
    out = run(
        services[1].create_plans(
            PlanRequest(store_code=STORE, requirements=make_req(6, 15000))
        )
    )
    codes = {i.code for plan in out.plans for i in plan.items}
    for item in restricted:
        assert item.code not in codes


def test_merchandise_never_enters_plans(services):
    menu = run(services[0].get_menu(STORE))
    merch = {i.code for i in menu.items if i.is_merchandise}
    assert merch, "快照里应有周边商品（帽子/丝巾/蘸酱）"
    out = run(
        services[1].create_plans(
            PlanRequest(store_code=STORE, requirements=make_req(4, 20000))
        )
    )
    codes = {i.code for plan in out.plans for i in plan.items}
    assert not (codes & merch)


# ------------------------------------------------------------ 缓存分区


def test_stores_cache_is_keyed_by_city_and_keyword(services):
    """换城市/关键词搜索不能命中上一次的缓存结果。"""
    menu_service = services[0]
    first = run(menu_service.get_stores("广州", "天河区体育西路"))
    second = run(menu_service.get_stores("深圳", "福田区"))
    # 演示快照只有一家门店，但两次请求的缓存键必须不同，不能复用同一个条目
    keys = set(menu_service._stores_cache)  # noqa: SLF001
    assert len(keys) == 2
    assert first.fetched_at and second.fetched_at
