import asyncio
import uuid

from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from app import reconcile
from app.core.config import get_settings
from tests.conftest import bearer, signup_and_login


def upload(client: TestClient, token: str, name: str, text: str) -> str:
    response = client.post(
        "/api/documents", files={"file": (name, text.encode())}, headers=bearer(token)
    )
    assert response.json()["status"] == "ready", response.text
    document_id: str = response.json()["id"]
    return document_id


def drop_collection() -> None:
    async def run() -> None:
        settings = get_settings()
        qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
        await qdrant.delete_collection(settings.qdrant_collection)
        await qdrant.close()

    asyncio.run(run())


def test_nothing_to_do_when_database_and_vectors_agree(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    upload(client, token, "a.txt", "Alpha document about volcanoes.")
    assert asyncio.run(reconcile.find_and_requeue()) == []


def test_documents_whose_vectors_are_gone_are_ingested_again(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    document_id = upload(client, token, "a.txt", "Alpha document about volcanoes.")
    failed = client.post(
        "/api/documents", files={"file": ("bad.pdf", b"%PDF-broken")}, headers=bearer(token)
    ).json()
    assert failed["status"] == "failed"

    # Simulate a restore where the vector snapshot is older than the database.
    drop_collection()
    assert asyncio.run(reconcile.find_and_requeue()) == [uuid.UUID(document_id)]
    status = client.get(f"/api/documents/{document_id}", headers=bearer(token)).json()["status"]
    assert status == "queued"

    reconcile.main()  # nothing left to find, but exercise the entry point
    from app.workers.tasks import ingest_document

    ingest_document.delay(document_id)  # what main() does for each id (inline in tests)
    document = client.get(f"/api/documents/{document_id}", headers=bearer(token)).json()
    assert document["status"] == "ready" and document["chunk_count"] == 1
    # Failed documents are left alone.
    still = client.get(f"/api/documents/{failed['id']}", headers=bearer(token)).json()
    assert still["status"] == "failed"
