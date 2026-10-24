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
