from fastapi.testclient import TestClient

from tests.conftest import bearer, signup_and_login


def test_api_key_lifecycle(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")

    created = client.post("/api/api-keys", json={"name": "laptop"}, headers=bearer(token))
    assert created.status_code == 201
    key = created.json()["key"]
    assert key.startswith("dq_")

    # The key authenticates on its own.
    me = client.get("/api/me", headers={"X-API-Key": key})
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"

    # Listing never reveals the secret.
    listed = client.get("/api/api-keys", headers=bearer(token))
    assert [item["name"] for item in listed.json()] == ["laptop"]
    assert key not in listed.text
    assert listed.json()[0]["last_used_at"] is not None

    revoked = client.delete(f"/api/api-keys/{created.json()['id']}", headers=bearer(token))
    assert revoked.status_code == 204
    assert client.get("/api/me", headers={"X-API-Key": key}).status_code == 401
    assert client.get("/api/api-keys", headers=bearer(token)).json() == []


def test_user_cannot_see_or_revoke_another_users_key(client: TestClient) -> None:
    alice = signup_and_login(client, "a@example.com")
    bob = signup_and_login(client, "b@example.com")
    created = client.post("/api/api-keys", json={"name": "alice key"}, headers=bearer(alice))

    assert client.get("/api/api-keys", headers=bearer(bob)).json() == []
    response = client.delete(f"/api/api-keys/{created.json()['id']}", headers=bearer(bob))
    assert response.status_code == 404
    assert client.get("/api/me", headers={"X-API-Key": created.json()["key"]}).status_code == 200
