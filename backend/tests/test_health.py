from fastapi.testclient import TestClient

from driftwatch.main import app


def test_health_endpoints() -> None:
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_metrics_endpoint_exposes_request_counter() -> None:
    with TestClient(app) as client:
        client.get("/health/live")
        response = client.get("/metrics")
    assert response.status_code == 200
    assert "driftwatch_http_requests_total" in response.text
