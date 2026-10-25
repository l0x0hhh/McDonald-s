"""持久化方案须属于当前配餐策略；使用真实 JSON 文件与离线 HTTP 请求。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.schemas import DietaryConstraint, PlanItemOut, PlanOut, Requirements
from backend.services import plan_store


def _plan(plan_id="policy-fixture"):
    return PlanOut(
        plan_id=plan_id, type="cheapest", title="当前策略方案", summary="",
        items=[PlanItemOut(
            code="burger", name="吉士汉堡", quantity=1, unit_price_fen=1000,
            original_unit_price_fen=1200, line_total_fen=1000, servings=1,
            spicy_level="mild", food_role="main", beverage_status="none",
            non_spicy_servings=1,
        )],
        subtotal_fen=1200, discount_fen=200, total_fen=1000, budget_fen=3000,
        remaining_fen=2000, servings_total=1, non_spicy_servings=1,
        price_basis="demo_snapshot",
    )


def _requirements():
    return Requirements(
        people=1, budget_fen=3000, budget_type="per_person", appetite="light",
        dietary_constraints=[DietaryConstraint(kind="non_spicy", people=1)],
        exclude_beverage=True, preferred_tags=["牛肉"], membership_status="active",
        meal_frequency_per_month=6, habit_horizon_months=3, habitual_tags=["汉堡"],
    )


def _replace_policy(path, plan_id, version):
    records = json.loads(path.read_text(encoding="utf-8"))
    if version is None:
        records[plan_id].pop("policy_version", None)
    else:
        records[plan_id]["policy_version"] = version
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("version", [None, 1, "obsolete-policy"], ids=["missing", "old", "unknown"])
def test_restart_rejects_records_without_current_policy_version(tmp_path, version):
    # 删除版本门控会令旧策略方案重新进入可结算集合。
    path = tmp_path / "plans.json"
    store = plan_store.PlanStore(path)
    plan_id = store.save(_plan(), "1400011", _requirements())
    _replace_policy(path, plan_id, version)

    restarted = plan_store.PlanStore(path)

    with pytest.raises(plan_store.PlanNotFoundError):
        restarted.get(plan_id)


def test_save_persists_policy_version_without_changing_public_plan(tmp_path):
    # 忘记写版本会导致刚生成的方案也在重启后被清理。
    path = tmp_path / "plans.json"
    store = plan_store.PlanStore(path)
    plan = _plan()
    public_before = plan.model_dump()
    plan_id = store.save(plan, "1400011", _requirements())

    record = json.loads(path.read_text(encoding="utf-8"))[plan_id]

    assert "policy_version" in record
    assert record["policy_version"] == plan_store.PLAN_POLICY_VERSION
    assert record["plan"] == public_before
    assert "policy_version" not in plan.model_dump()


def test_current_policy_restart_preserves_requirements_store_and_original_ttl(tmp_path, monkeypatch):
    # 重启若重置 created_at，会延长过期价格的可用时间。
    now = [1000.0]
    monkeypatch.setattr(plan_store, "time", SimpleNamespace(time=lambda: now[0]))
    path = tmp_path / "plans.json"
    store = plan_store.PlanStore(path)
    plan = _plan()
    requirements = _requirements()
    plan_id = store.save(plan, "1400011", requirements)
    now[0] += plan_store.PLAN_TTL_SECONDS - 1

    restarted = plan_store.PlanStore(path)
    saved_plan, store_code, saved_requirements = restarted.get(plan_id)

    assert saved_plan.model_dump() == plan.model_dump()
    assert store_code == "1400011"
    assert saved_requirements.model_dump() == requirements.model_dump()
    assert json.loads(path.read_text(encoding="utf-8"))[plan_id]["created_at"] == 1000.0
    now[0] += 1
    assert restarted.get(plan_id)[0].plan_id == plan_id
    now[0] += 0.001
    with pytest.raises(plan_store.PlanExpiredError):
        restarted.get(plan_id)


def test_mixed_cache_keeps_only_current_policy_records(tmp_path):
    path = tmp_path / "plans.json"
    store = plan_store.PlanStore(path)
    current_id = store.save(_plan("current"), "1400011", _requirements())
    old_id = store.save(_plan("old"), "1400011", _requirements())
    _replace_policy(path, old_id, 1)

    restarted = plan_store.PlanStore(path)

    assert restarted.get(current_id)[0].plan_id == current_id
    with pytest.raises(plan_store.PlanNotFoundError):
        restarted.get(old_id)
    restarted.save(_plan("fresh"), "1400011", _requirements())
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"current", "fresh"}


class OfficialProvider:
    def __init__(self):
        self.creates = 0

    async def calculate_price(self, arguments):
        return {"success": True, "data": {"price": 1000, "originalPrice": 1200, "discount": 200,
                 "takeWayList": [{"code": "pickup", "title": "外带"}]}}

    async def create_order(self, arguments):
        self.creates += 1
        return {"success": True, "data": {"orderId": "official-policy-order",
                 "payH5Url": "https://m.mcd.cn/mcp/scanToPay?orderId=official-policy-order",
                 "orderDetail": {"orderStatus": "待支付", "status": "10"}}}


def _quoted_setup(tmp_path):
    from backend.services.checkout_service import CheckoutService
    from backend.services.order_repository import OrderRepository
    path = tmp_path / "plans.json"
    store = plan_store.PlanStore(path)
    provider = OfficialProvider()
    repository = OrderRepository(tmp_path / "orders.sqlite3")
    checkout = CheckoutService(plans=store, provider=provider, repository=repository)
    plan = asyncio.run(checkout.quote(_plan(), "1400011", _requirements()))
    store.save(plan, "1400011", _requirements())
    payload = {"plan_id": plan.plan_id, "quote_id": plan.quote_id,
               "take_way_code": "pickup", "idempotency_key": "policy-request-123"}
    return path, plan, provider, repository, payload


@pytest.mark.parametrize("version", [None, 1], ids=["missing", "old"])
def test_orders_rejects_obsolete_persisted_plan_after_restart(tmp_path, monkeypatch, version):
    from backend.api import routes
    from backend.services.checkout_service import CheckoutService
    path, plan, provider, repository, payload = _quoted_setup(tmp_path)
    _replace_policy(path, plan.plan_id, version)
    restarted = plan_store.PlanStore(path)
    checkout = CheckoutService(plans=restarted, provider=provider, repository=repository)
    monkeypatch.setattr(routes, "plan_service", SimpleNamespace(checkout=checkout))
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post("/api/v1/orders", json=payload)
    assert response.status_code == 404
    assert response.json()["code"] == "E_PLAN_NOT_FOUND"
    assert provider.creates == 0


def test_current_persisted_quote_creates_official_pending_order_after_restart(tmp_path, monkeypatch):
    from backend.api import routes
    from backend.services.checkout_service import CheckoutService
    path, plan, provider, repository, payload = _quoted_setup(tmp_path)
    checkout = CheckoutService(plans=plan_store.PlanStore(path), provider=provider, repository=repository)
    monkeypatch.setattr(routes, "plan_service", SimpleNamespace(checkout=checkout))
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post("/api/v1/orders", json=payload)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "pending_payment"
    assert body["order_id"] == "official-policy-order"
    assert body["payment_url"].startswith("https://m.mcd.cn/")
    assert body["total_fen"] == 1000
    assert body["store_code"] == "1400011"
    assert body["plan_id"] == plan.plan_id
    assert "is_demo" not in body
    assert provider.creates == 1
