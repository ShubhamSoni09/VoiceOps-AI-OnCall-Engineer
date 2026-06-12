from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_root() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "checkout-api"


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_charge_endpoint() -> None:
    response = client.get("/v2/charge", params={"amount": 10})
    assert response.status_code == 200
    assert response.json()["status"] == "charged"


def test_metrics_schema() -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert "error_rate" in body
    assert body["error_rate"] < 1.0
