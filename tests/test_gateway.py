import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateway import configuration_error, create_gateway_app


@pytest.mark.parametrize("url", [
    "", "http://127.0.0.1:8000", "http://127.0.0.1.8000",
    "https://127.0.0.1:8000", "https://localhost:8000", "https://10.0.0.1",
    "https://[::1]:8000", "https://user:secret@backend.example", "https://backend.example?key=value",
])
def test_deployed_gateway_rejects_unusable_backend(url):
    with TestClient(create_gateway_app(url, deployed=True)) as client:
        response = client.get("/api/customers")
    assert response.status_code == 503
    assert response.json()["detail"]


def test_local_gateway_allows_local_backend():
    assert configuration_error("http://127.0.0.1:8000", False) is None


def test_proxy_forwards_payment_body_query_and_auth_without_caching(monkeypatch):
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(201, json={"status": "AWAITING_PAYMENT"}, headers={"set-cookie": "session=test; Secure", "cache-control": "public, max-age=600"})

    client_class = httpx.AsyncClient
    monkeypatch.setattr("app.gateway.httpx.AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    with TestClient(create_gateway_app("https://backend.example/", deployed=True)) as client:
        response = client.post("/payment/sync?test=1", json={"order_id": 12}, headers={"X-Tokri-Secret": "test-secret"})
    assert response.status_code == 201
    assert response.json() == {"status": "AWAITING_PAYMENT"}
    assert str(seen[0].url) == "https://backend.example/payment/sync?test=1"
    assert seen[0].headers["x-tokri-secret"] == "test-secret"
    assert seen[0].content == b'{"order_id":12}'
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["set-cookie"] == "session=test; Secure"


@pytest.mark.parametrize("exc,status", [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)])
def test_unreachable_backend_has_actionable_error(monkeypatch, exc, status):
    async def handle(request):
        raise exc("unavailable", request=request)

    client_class = httpx.AsyncClient
    monkeypatch.setattr("app.gateway.httpx.AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    with TestClient(create_gateway_app("https://backend.example", deployed=True)) as client:
        response = client.get("/api/customers")
    assert response.status_code == status
    assert "tunnel" in response.json()["detail"]


def test_gateway_rejects_self_proxy():
    with TestClient(create_gateway_app("https://dashboard.example", deployed=True), base_url="https://dashboard.example") as client:
        response = client.get("/api/customers")
    assert response.status_code == 503
    assert "loop" in response.json()["detail"]


def test_gateway_serves_real_dashboard_and_backend_call_flow(fresh_db, monkeypatch):
    from app.main import app as backend

    client_class = httpx.AsyncClient
    monkeypatch.setattr("app.gateway.httpx.AsyncClient", lambda **kwargs: client_class(transport=httpx.ASGITransport(app=backend), **kwargs))
    with TestClient(create_gateway_app("https://backend.example", deployed=True)) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "<title>Tokri</title>" in dashboard.text
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/customers").json()
        start = client.post("/dev/start-call", data={"customer_id": 7})
        assert start.status_code == 200
        order_id = start.json()["order_id"]
        basket = client.post("/dev/simulate-voice", data={"order_id": order_id, "text": "toor dal"})
        assert basket.json()["status"] == "BASKET_DRAFT"
        payment = client.post("/dev/pay", data={"order_id": order_id, "payment_choice": "full_now"})
        assert payment.json()["status"] == "AWAITING_PAYMENT"
        assert payment.json()["sms_notification"]["status"] == "mocked"


@pytest.mark.parametrize("system_flag", ["1", ""])
@pytest.mark.parametrize("backend_url,expected_status", [
    ("https://backend.example", 200), ("", 503), ("http://127.0.0.1.8000", 503),
])
def test_vercel_entrypoint_never_starts_local_database(monkeypatch, system_flag, backend_url, expected_status):
    monkeypatch.setenv("VERCEL", system_flag)
    monkeypatch.delenv("API_URL", raising=False)
    monkeypatch.setenv("VITE_API_URL", backend_url)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    import builtins
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "app.main":
            pytest.fail("Vercel gateway tried to initialize the local database")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    spec = importlib.util.spec_from_file_location("gateway_entrypoint_test", Path(__file__).resolve().parents[1] / "index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        response = client.get("/deployment-health")
        assert response.status_code == expected_status
        assert response.json()["mode"] == "gateway"
        if expected_status == 503:
            assert client.get("/api/orders").status_code == 503
