import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_all_services_up(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.json() == {
        "status": "ok",
        "checks": {"postgres": "ok", "valkey": "ok", "qdrant": "ok"},
    }
    assert response.status_code == 200


def test_readyz_reports_unreachable_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCQA_QDRANT_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("DOCQA_VALKEY_URL", "valkey://127.0.0.1:1/0")
    monkeypatch.setenv("DOCQA_DATABASE_URL", "postgresql+asyncpg://x:x@127.0.0.1:1/x")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            response = test_client.get("/readyz")
    finally:
        get_settings.cache_clear()
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert all(value.startswith("error") for value in body["checks"].values())


def test_readyz_checks_model_servers_only_when_real_ones_are_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "chat_model" not in client.get("/readyz").json()["checks"]

    monkeypatch.setattr(get_settings(), "chat_provider", "openai")
    monkeypatch.setattr(get_settings(), "chat_url", "http://127.0.0.1:1")
    response = client.get("/readyz")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["chat_model"].startswith("error") and checks["postgres"] == "ok"
    assert "embedding_model" not in checks
