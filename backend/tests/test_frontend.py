from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def spa_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app shell</html>")
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log('app')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path.parent / "secret.txt").write_text("outside the web root")
    monkeypatch.setattr(get_settings(), "static_dir", tmp_path)
    with TestClient(create_app()) as test_client:
        yield test_client


def test_serves_app_shell_assets_and_client_routes(spa_client: TestClient) -> None:
    for path in ("/", "/chat", "/chat/01234567-89ab-cdef-0123-456789abcdef", "/documents"):
        response = spa_client.get(path)
        assert response.status_code == 200 and "app shell" in response.text, path
        assert response.headers["cache-control"] == "no-cache"
    assert "console.log" in spa_client.get("/assets/app-abc123.js").text
    assert spa_client.get("/favicon.svg").text == "<svg/>"


def test_api_routes_still_win_and_unknown_api_paths_are_404(spa_client: TestClient) -> None:
    assert spa_client.get("/healthz").json() == {"status": "ok"}
    assert spa_client.get("/api/me").status_code == 401
    missing = spa_client.get("/api/does-not-exist")
    assert missing.status_code == 404 and "app shell" not in missing.text


def test_cannot_read_files_outside_the_web_root(spa_client: TestClient) -> None:
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        assert "outside the web root" not in spa_client.get(path).text


def test_no_frontend_routes_without_static_dir(client: TestClient) -> None:
    assert client.get("/chat").status_code == 404
