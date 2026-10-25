"""HTTP wiring for server-owned quotes and official pending-payment orders."""
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import routes
from backend.schemas import OrderOut


class Checkout:
    def __init__(self):
        self.calls = []

    async def create_order(self, payload):
        self.calls.append(payload)
        return OrderOut(order_id="official-42", plan_id=payload.plan_id, store_code="1400011",
                        total_fen=3480, status="pending_payment",
                        payment_url="https://m.mcd.cn/mcp/scanToPay?id=official-42",
                        created_at="2026-10-25T11:00:00+08:00")

    async def get_order(self, order_id):
        self.calls.append(order_id)
        return OrderOut(order_id=order_id, plan_id="p", store_code="1400011",
                        total_fen=3480, status="preparing", created_at="2026-10-25T11:00:00+08:00")


def client_for(monkeypatch):
    checkout = Checkout()
    monkeypatch.setattr(routes, "plan_service", SimpleNamespace(checkout=checkout))
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), checkout


def test_choose_plan_delegates_to_checkout_instead_of_building_simulated_order(monkeypatch):
    client, checkout = client_for(monkeypatch)
    response = client.post("/api/v1/orders", json={"plan_id": "p", "quote_id": "q", "take_way_code": "pickup",
                                                 "idempotency_key": "request-12345678"})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "pending_payment"
    assert response.json()["data"]["order_id"] == "official-42"
    assert response.json()["data"]["payment_url"].startswith("https://m.mcd.cn/")
    assert len(checkout.calls) == 1 and checkout.calls[0].quote_id == "q"


def test_order_status_comes_from_checkout_query(monkeypatch):
    client, checkout = client_for(monkeypatch)
    response = client.get("/api/v1/orders/official-42")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "preparing"
    assert checkout.calls == ["official-42"]
