import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.conftest import bearer, signup_and_login

PASSWORD = "correct horse 42"


def _refresh_with(client: TestClient, cookie: str) -> int:
    """Call refresh sending exactly this cookie, bypassing the client's cookie jar."""
    client.cookies.clear()
    response = client.post("/api/auth/refresh", headers={"Cookie": f"docqa_refresh={cookie}"})
    return response.status_code


def _signup(client: TestClient, **overrides: str) -> int:
    body = {"email": "a@example.com", "password": PASSWORD, "invite_code": "test-invite"}
    return client.post("/api/auth/signup", json=body | overrides).status_code


def test_signup_requires_invite_code(client: TestClient) -> None:
    assert _signup(client, invite_code="wrong") == 403


def test_signup_closed_when_no_invite_code_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "invite_code", "")
    assert _signup(client, invite_code="") == 403


def test_signup_rejects_short_password_and_duplicates(client: TestClient) -> None:
    assert _signup(client, password="short") == 422
    assert _signup(client) == 201
    assert _signup(client, email="A@Example.com") == 409


def test_login_and_me(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    response = client.get("/api/me", headers=bearer(token))
    assert response.status_code == 200
    assert response.json()["email"] == "a@example.com"
    assert "password" not in response.text


def test_login_failures_are_indistinguishable(client: TestClient) -> None:
    signup_and_login(client, "a@example.com")
    wrong_password = client.post(
        "/api/auth/login", json={"email": "a@example.com", "password": "nope nope nope"}
    )
    unknown_user = client.post(
        "/api/auth/login", json={"email": "ghost@example.com", "password": PASSWORD}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer not-a-jwt"}, {"X-API-Key": "dq_bogus"}]
)
def test_me_rejects_missing_or_bad_credentials(client: TestClient, headers: dict[str, str]) -> None:
    assert client.get("/api/me", headers=headers).status_code == 401


def test_refresh_rotates_and_detects_reuse(client: TestClient) -> None:
    signup_and_login(client, "a@example.com")
    first_cookie = client.cookies.get("docqa_refresh")
    assert first_cookie

    rotated = client.post("/api/auth/refresh")
    assert rotated.status_code == 200
    assert client.get("/api/me", headers=bearer(rotated.json()["access_token"])).status_code == 200
    second_cookie = client.cookies.get("docqa_refresh")
    assert second_cookie and second_cookie != first_cookie

    # Replaying the old token fails and revokes the whole family, including the new one.
    assert _refresh_with(client, first_cookie) == 401
    assert _refresh_with(client, second_cookie) == 401


def test_logout_revokes_refresh_token(client: TestClient) -> None:
    signup_and_login(client, "a@example.com")
    cookie = client.cookies.get("docqa_refresh")
    assert cookie
    assert client.post("/api/auth/logout").status_code == 204
    assert _refresh_with(client, cookie) == 401


def test_login_is_rate_limited_per_ip(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "auth_rate_limit_per_minute", 3)
    body = {"email": "ghost@example.com", "password": PASSWORD}
    statuses = [client.post("/api/auth/login", json=body).status_code for _ in range(4)]
    assert statuses == [401, 401, 401, 429]


def test_api_is_rate_limited_per_user(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    alice = signup_and_login(client, "a@example.com")
    bob = signup_and_login(client, "b@example.com")
    monkeypatch.setattr(get_settings(), "api_rate_limit_per_minute", 2)
    statuses = [client.get("/api/me", headers=bearer(alice)).status_code for _ in range(3)]
    assert statuses == [200, 200, 429]
    assert client.get("/api/me", headers=bearer(bob)).status_code == 200
