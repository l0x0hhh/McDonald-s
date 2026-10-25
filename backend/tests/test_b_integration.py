# 回归说明：部分饮料需求保留总人数并追问，在线模型不能绕过守卫或冒充回退成功。
"""注入在线模型/上游的聊天、方案核价与真实待支付契约验收。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import routes
from backend.config import settings
from backend.main import app
from backend.services.menu_service import MenuService
from backend.services.plan_service import PlanService
from backend.services.plan_store import PlanStore


def _client(monkeypatch, tmp_path):
    from backend.tests.online_fakes import make_services, inject_chat
    menus, service = make_services(tmp_path)
    inject_chat(monkeypatch)
    monkeypatch.setattr(routes, "menu_service", menus)
    monkeypatch.setattr(routes, "plan_service", service)
    return TestClient(app), service


def test_browser_entry_redirects_to_current_online_frontend(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "http://127.0.0.1:5173/"


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


def test_chat_plans_and_pending_payment_respect_no_beverage(monkeypatch, tmp_path):
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
    assert body["algorithm_source"] == "llm_skill"
    assert not any("demo_sample" in note for note in body["suggestions"])
    assert body["plans"]
    for plan in body["plans"]:
        assert plan["total_fen"] <= 10000
        assert plan["servings_total"] >= 2
        assert plan["non_spicy_servings"] >= 1
        assert all(line["beverage_status"] == "none" for line in plan["items"])
        assert plan["membership_comparison"] is None

    first_id = body["plans"][0]["plan_id"]
    order = client.post("/api/v1/orders", json={"plan_id": first_id, "quote_id": body["plans"][0]["quote_id"],
                                                    "take_way_code": "pickup", "idempotency_key": "fixture-order-key-001"})
    assert order.status_code == 200
    assert order.json()["data"]["status"] == "pending_payment"
    assert order.json()["data"]["payment_url"].startswith("https://m.mcd.cn/")
    assert "is_demo" not in order.json()["data"]
    assert order.json()["data"]["total_fen"] == body["plans"][0]["total_fen"]
    assert service.store.get(first_id)[0].plan_id == first_id


def test_main_portions_are_not_product_row_count(monkeypatch, tmp_path):
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
    assert client.post("/api/v1/orders", json={"plan_id": plan_id, "quote_id": first.json()["data"]["plans"][0]["quote_id"],
                                                   "take_way_code": "pickup", "idempotency_key": "expired-plan-key-001"}).status_code == 404

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


def test_partial_beverage_chat_preserves_people_and_waits_for_confirmation(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    first = client.post("/api/v1/chat", json={
        "message": "我们4个人，总预算120元，其中2人不喝饮料",
    }).json()["data"]
    assert first["draft"]["people"] == 4
    assert first["draft"]["exclude_beverage"] is False
    assert first["ready"] is False
    assert first["requirements"] is None
    assert "beverage_scope" in first["missing_fields"]
    resolved = client.post("/api/v1/chat", json={
        "message": "整单不限制饮料", "current": first["draft"],
    }).json()["data"]
    assert resolved["ready"] is True
    assert resolved["requirements"]["people"] == 4
    response = client.post("/api/v1/plans", json={
        "store_code": "1400011", "requirements": resolved["requirements"],
    })
    assert response.status_code == 200
    plans = response.json()["data"]["plans"]
    assert plans
    assert all(plan["servings_total"] >= 4 and plan["total_fen"] <= 12000 for plan in plans)


def test_negation_replacement_and_named_membership_work_through_chat(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    first = client.post("/api/v1/chat", json={
        "message": "2人，总预算100元，喜欢牛肉",
    }).json()["data"]
    changed = client.post("/api/v1/chat", json={
        "message": "不是大饭量，不喜欢牛肉，改成鸡肉，我有麦金卡", "current": first["draft"],
    }).json()["data"]
    assert changed["ready"] is True
    assert changed["requirements"]["people"] == 2
    assert changed["requirements"]["appetite"] == "normal"
    assert changed["requirements"]["preferred_tags"] == ["chicken"]
    assert changed["requirements"]["membership_status"] == "active"


def test_online_model_cannot_overwrite_group_people_in_api(monkeypatch, tmp_path):
    from backend import llm
    client, _ = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "data_mode", "auto")
    monkeypatch.setattr(llm, "_request_model", lambda *args: {
        "updates": {"people": 2, "exclude_beverage": True},
        "evidence": {"people": "2人", "exclude_beverage": "不喝饮料"},
    })
    data = client.post("/api/v1/chat", json={
        "message": "其中2人不喝饮料",
        "current": {"people": 4, "budget_type": "total", "budget_fen": 12000},
    }).json()["data"]
    assert data["parse_source"] == "llm"
    assert data["draft"]["people"] == 4
    assert data["draft"]["exclude_beverage"] is False
    assert data["ready"] is False
    assert "beverage_scope" in data["missing_fields"]


def test_online_failure_returns_explicit_error_not_rule_success(monkeypatch, tmp_path):
    from backend import llm
    client, _ = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "data_mode", "auto")

    def failed_model(*args):
        raise llm.ExtractionError("模型请求失败")

    monkeypatch.setattr(llm, "_request_model", failed_model)
    response = client.post("/api/v1/chat", json={
        "message": "喜欢鸡肉",
        "current": {"people": 4, "budget_type": "total", "budget_fen": 12000},
    })
    assert response.status_code == 502
    assert response.json()["data"] is None
    assert response.json()["code"] == "E_LLM_REQUEST_FAILED"
