"""Tests run against the real services from compose.dev.yaml, in a separate database.

Environment is set before the app is imported so settings pick it up.
"""

import asyncio
import os
import shutil
import tempfile
from collections.abc import Iterator

TEST_DATABASE = "docqa_test"
_ADMIN_URL = os.environ.get(
    "DOCQA_DATABASE_URL", "postgresql+asyncpg://docqa:docqa@localhost:55432/docqa"
)
_TEST_URL = _ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DATABASE}"
_VALKEY_URL = (
    os.environ.get("DOCQA_VALKEY_URL", "valkey://localhost:56379/0").rsplit("/", 1)[0] + "/15"
)

os.environ["DOCQA_ENVIRONMENT"] = "test"
os.environ["DOCQA_DATABASE_URL"] = _TEST_URL
os.environ["DOCQA_VALKEY_URL"] = _VALKEY_URL
os.environ["DOCQA_INVITE_CODE"] = "test-invite"
os.environ["DOCQA_CELERY_EAGER"] = "true"  # ingestion runs inline, no worker needed
os.environ["DOCQA_QDRANT_COLLECTION"] = "chunks_test"
os.environ["DOCQA_EMBEDDING_PROVIDER"] = "fake"
os.environ["DOCQA_EMBEDDING_DIMENSION"] = "64"
os.environ["DOCQA_STORAGE_DIR"] = tempfile.mkdtemp(prefix="docqa-test-")

import pytest  # noqa: E402
import valkey.asyncio as valkey  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from qdrant_client import AsyncQdrantClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import app.models  # noqa: E402, F401
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.main import create_app  # noqa: E402


async def _create_schema() -> None:
    admin = create_async_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with admin.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DATABASE}
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    await admin.dispose()

    engine = create_async_engine(_TEST_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _reset_state() -> None:
    engine = create_async_engine(_TEST_URL, poolclass=NullPool)
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    client = valkey.from_url(_VALKEY_URL)  # type: ignore[no-untyped-call]
    await client.flushdb()
    await client.aclose()
    settings = get_settings()
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    await qdrant.delete_collection(settings.qdrant_collection)
    await qdrant.close()
    shutil.rmtree(settings.storage_dir, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    asyncio.run(_create_schema())


@pytest.fixture(autouse=True)
def _clean(_schema: None) -> None:
    asyncio.run(_reset_state())


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def signup_and_login(client: TestClient, email: str, password: str = "correct horse 42") -> str:
    """Create a user and return an access token."""
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": password, "invite_code": "test-invite"},
    )
    assert response.status_code == 201, response.text
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
