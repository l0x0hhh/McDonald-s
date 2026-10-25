"""Contract validation for online planning and real pending-payment orders."""
import pytest
from pydantic import ValidationError
from backend.schemas import OrderIn, OrderOut, PlanItemOut


def test_order_input_requires_server_quote_and_idempotency_fields():
    with pytest.raises(ValidationError):
        OrderIn(plan_id="plan")


def test_order_input_rejects_client_supplied_amount():
    with pytest.raises(ValidationError):
        OrderIn(plan_id="p", quote_id="q", take_way_code="pickup", idempotency_key="request-12345678", total_fen=1)


def test_pending_payment_contract_has_real_link_not_demo_status():
    order = OrderOut(order_id="official", plan_id="p", store_code="s", total_fen=3480,
                     status="pending_payment", payment_url="https://m.mcd.cn/mcp/scanToPay?orderId=official",
                     expires_at=None, created_at="2026-10-25T11:00:00+08:00")
    assert order.status == "pending_payment" and order.payment_url.startswith("https://m.mcd.cn/")
    assert "is_demo" not in order.model_dump()


def test_planning_deadline_has_a_bounded_gateway_timeout_response():
    import json
    from backend.api.routes import service_failure
    from backend.services.llm_planner import PlanningError
    response = service_failure(PlanningError("E_PLANNING_TIMEOUT", "本次搭配核对超时，请重试。"), "fallback")
    assert response.status_code == 504
    assert json.loads(response.body)["code"] == "E_PLANNING_TIMEOUT"
