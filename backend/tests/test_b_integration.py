"""从聊天草稿到正式 Skill、服务端校验及模拟结算的接口验收。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import routes
from backend.config import settings
from backend.main import app
from backend.services.menu_service import MenuService
from backend.services.plan_service import PlanService
from backend.services.plan_store import PlanStore


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_mode", "demo")
    menus = MenuService()
    service = PlanService(menus=menus, store=PlanStore(tmp_path / "plans.json"))
    monkeypatch.setattr(routes, "menu_service", menus)
    monkeypatch.setattr(routes, "plan_service", service)
    return TestClient(app), service


def test_browser_entry_serves_request_form(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="request-form"' in response.text
    assert "/api/v1/chat" in response.text
    assert "/api/v1/plans" in response.text
    assert "主食总计" in response.text


def test_new_chat_does_not_inherit_previous_non_spicy_constraint(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    first = client.post("/api/v1/chat", json={"message": "2人，总预算100元，1人不吃辣"}).json()["data"]
    assert first["requirements"]["dietary_constraints"][0]["people"] == 1
    new = client.post("/api/v1/chat", json={"message": "3人，总预算60元"}).json()["data"]
    assert new["requirements"]["people"] == 3
    assert new["requirements"]["dietary_constraints"] == []
    assert new["requirements"]["exclude_beverage"] is False
    continued = client.post("/api/v1/chat", json={
        "message": "3人，总预算60元", "current": first["draft"],
    }).json()["data"]
    assert continued["requirements"]["dietary_constraints"][0]["people"] == 1


def test_chat_plans_and_simulated_order_respect_no_beverage(monkeypatch, tmp_path):
    client, service = _client(monkeypatch, tmp_path)
    first = client.post("/api/v1/chat", json={"message": "2人，不要饮料"})
    assert first.status_code == 200
    partial = first.json()["data"]
    assert partial["ready"] is False
    assert partial["draft"]["people"] == 2
    assert partial["draft"]["exclude_beverage"] is True
    assert "budget" in partial["missing_fields"]

    second = client.post("/api/v1/chat", json={
        "message": "总预算100元，其中1人不吃辣",
        "current": partial["draft"],
    })
    assert second.status_code == 200
    collected = second.json()["data"]
    assert collected["ready"] is True
    requirement = collected["requirements"]
    assert requirement["exclude_beverage"] is True
    assert requirement["dietary_constraints"][0]["people"] == 1

    response = client.post("/api/v1/plans", json={
        "store_code": "1400011", "requirements": requirement,
    })
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["algorithm_source"] == "skill"
    assert not any("demo_sample" in note for note in body["suggestions"])
    assert body["plans"]
    for plan in body["plans"]:
        assert plan["total_fen"] <= 10000
        assert plan["servings_total"] >= 2
        assert plan["non_spicy_servings"] >= 1
        assert all(line["beverage_status"] == "none" for line in plan["items"])
        assert plan["membership_comparison"]["status"] == "unavailable"

    first_id = body["plans"][0]["plan_id"]
    order = client.post("/api/v1/orders", json={"plan_id": first_id})
    assert order.status_code == 200
    assert order.json()["data"]["is_demo"] is True
    assert order.json()["data"]["total_fen"] == body["plans"][0]["total_fen"]
    assert service.store.get(first_id)[0].plan_id == first_id


def test_demo_main_portions_are_not_product_row_count(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    for people in range(1, 8):
        response = client.post("/api/v1/plans", json={
            "store_code": "1400011", "requirements": {
                "people": people, "budget_type": "total", "budget_fen": people * 5000,
                "exclude_beverage": True,
            },
        })
        assert response.status_code == 200
        plans = response.json()["data"]["plans"]
        assert plans
        for plan in plans:
            assert plan["servings_total"] >= people
            assert plan["servings_total"] == sum(
                item["quantity"] * item["servings"] for item in plan["items"]
            )


def test_new_requirement_invalidates_old_price_and_rejects_unknown_constraint(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    request = {"store_code": "1400011", "requirements": {
        "people": 1, "budget_type": "total", "budget_fen": 5000,
        "appetite": "light", "exclude_beverage": True,
    }}
    first = client.post("/api/v1/plans", json=request)
    assert first.status_code == 200
    plan_id = first.json()["data"]["plans"][0]["plan_id"]
    new_request = {**request, "requirements": {**request["requirements"], "budget_fen": 1}}
    changed = client.post("/api/v1/plans", json=new_request)
    assert changed.status_code == 200
    assert changed.json()["data"]["plans"] == []
    assert changed.json()["data"]["unsatisfiable_reason"]
    assert client.post("/api/v1/orders", json={"plan_id": plan_id}).status_code == 404

    unsupported = {**request, "requirements": {**request["requirements"], "avoid_peanuts": True}}
    assert client.post("/api/v1/plans", json=unsupported).status_code == 422


def test_campaign_endpoint_reports_missing_upstream_facts(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    response = client.get("/api/v1/campaigns", params={"store_code": "1400011", "preferred_tags": "chicken"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "unavailable"
    assert data["items"] == []
    assert data["reason"]
