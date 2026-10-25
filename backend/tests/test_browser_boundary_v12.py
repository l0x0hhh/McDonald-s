"""Real-order endpoints must not be callable by arbitrary browser origins."""
from fastapi.testclient import TestClient
from backend.main import app


def test_unrelated_origin_cannot_preflight_a_real_order():
    response = TestClient(app).options("/api/v1/orders", headers={
        "Origin": "https://unrelated.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_local_frontend_can_preflight_a_real_order():
    response = TestClient(app).options("/api/v1/orders", headers={
        "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
