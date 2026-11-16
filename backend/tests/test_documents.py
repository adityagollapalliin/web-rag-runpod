import asyncio
import io
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient
from reportlab.pdfgen import canvas

from app.core.config import get_settings
from app.services.embedding import FakeEmbedder
from app.services.vector_store import SearchHit, VectorStore
from tests.conftest import bearer, signup_and_login

ALICE_TEXT = (
    "Photosynthesis converts sunlight into chemical energy inside chloroplasts. "
    "Plants absorb carbon dioxide and release oxygen during this process."
)
BOB_TEXT = (
    "The quarterly budget allocates funds to marketing and engineering. "
    "Invoices must be approved by the finance director before payment."
)


def make_pdf(pages: list[str]) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for text in pages:
        if text:
            pdf.drawString(72, 720, text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def upload(client: TestClient, token: str, name: str, data: bytes) -> tuple[int, dict[str, Any]]:
    response = client.post("/api/documents", files={"file": (name, data)}, headers=bearer(token))
    return response.status_code, response.json()


def user_id(client: TestClient, token: str) -> uuid.UUID:
    return uuid.UUID(client.get("/api/me", headers=bearer(token)).json()["id"])


def with_store[T](action: Callable[[VectorStore, FakeEmbedder], Awaitable[T]]) -> T:
    """Run `action(store, embedder)` against the test collection."""

    async def run() -> T:
        settings = get_settings()
        qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
        try:
            store = VectorStore(qdrant, settings.qdrant_collection, settings.embedding_dimension)
            return await action(store, FakeEmbedder(settings.embedding_dimension))
        finally:
            await qdrant.close()

    return asyncio.run(run())


def search(tenant: uuid.UUID, query: str) -> list[SearchHit]:
    async def action(store: VectorStore, embedder: FakeEmbedder) -> list[SearchHit]:
        return await store.hybrid_search(tenant, query, (await embedder.embed([query]))[0])

    return with_store(action)


def count(tenant: uuid.UUID) -> int:
    async def action(store: VectorStore, _: FakeEmbedder) -> int:
        return await store.count(tenant)

    return with_store(action)


def test_upload_text_file_becomes_ready_and_searchable(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    code, document = upload(client, token, "plants.txt", ALICE_TEXT.encode())

    assert code == 202
    assert document["status"] == "ready", document
    assert document["kind"] == "text"
    assert document["page_count"] == 1 and document["chunk_count"] == 1

    fetched = client.get(f"/api/documents/{document['id']}", headers=bearer(token))
    assert fetched.status_code == 200 and fetched.json()["status"] == "ready"
    assert [d["id"] for d in client.get("/api/documents", headers=bearer(token)).json()] == [
        document["id"]
    ]

    hits = search(user_id(client, token), "chloroplasts")
    assert len(hits) == 1
    assert hits[0].document_id == uuid.UUID(document["id"])
    assert hits[0].filename == "plants.txt" and "chloroplasts" in hits[0].text


def test_upload_pdf_tracks_pages(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    pdf = make_pdf(["Alpha page about volcanoes.", "Beta page about glaciers."])
    code, document = upload(client, token, "geo.pdf", pdf)

    assert code == 202 and document["status"] == "ready", document
    assert document["kind"] == "pdf" and document["page_count"] == 2

    hits = search(user_id(client, token), "glaciers")
    assert hits and "glaciers" in hits[0].text
    assert hits[0].page_start == 1 and hits[0].page_end == 2


def test_keyword_search_ranks_the_matching_chunk_first(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "chunk_max_chars", 120)
    monkeypatch.setattr(get_settings(), "chunk_overlap_chars", 0)
    token = signup_and_login(client, "a@example.com")
    text = " ".join(f"Filler sentence number {i} talks about nothing special." for i in range(30))
    text += " The zygomorphic orchid blooms at midnight. " + text
    _, document = upload(client, token, "long.txt", text.encode())
    assert document["status"] == "ready" and document["chunk_count"] > 10

    hits = search(user_id(client, token), "zygomorphic orchid")
    assert "zygomorphic" in hits[0].text


def test_pdf_without_text_fails_with_a_reason(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    code, document = upload(client, token, "scan.pdf", make_pdf([""]))
    assert code == 202
    assert document["status"] == "failed"
    assert "No text found" in document["error"]
    assert count(user_id(client, token)) == 0


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("malware.exe", b"MZ\x90\x00"),
        ("fake.pdf", b"this is not a pdf"),
        ("binary.txt", b"abc\x00def"),
        ("empty.txt", b""),
    ],
)
def test_unsupported_uploads_are_rejected(client: TestClient, name: str, data: bytes) -> None:
    token = signup_and_login(client, "a@example.com")
    code, _ = upload(client, token, name, data)
    assert code == 415
    assert client.get("/api/documents", headers=bearer(token)).json() == []
    assert not any(p.is_file() for p in Path(get_settings().storage_dir).rglob("*"))


def test_oversized_upload_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    token = signup_and_login(client, "a@example.com")
    code, _ = upload(client, token, "big.txt", b"a" * (1024 * 1024 + 1))
    assert code == 413
    assert not any(p.is_file() for p in Path(get_settings().storage_dir).rglob("*"))


def test_duplicate_upload_is_rejected_per_user(client: TestClient) -> None:
    alice = signup_and_login(client, "a@example.com")
    bob = signup_and_login(client, "b@example.com")
    assert upload(client, alice, "one.txt", ALICE_TEXT.encode())[0] == 202
    assert upload(client, alice, "renamed.txt", ALICE_TEXT.encode())[0] == 409
    assert upload(client, bob, "one.txt", ALICE_TEXT.encode())[0] == 202


def test_upload_requires_authentication(client: TestClient) -> None:
    assert client.post("/api/documents", files={"file": ("a.txt", b"hi")}).status_code == 401


def test_users_never_see_each_others_documents_or_chunks(client: TestClient) -> None:
    alice = signup_and_login(client, "a@example.com")
    bob = signup_and_login(client, "b@example.com")
    _, alice_doc = upload(client, alice, "plants.txt", ALICE_TEXT.encode())
    _, bob_doc = upload(client, bob, "budget.txt", BOB_TEXT.encode())
    alice_id, bob_id = user_id(client, alice), user_id(client, bob)

    # API: Bob cannot read, list or delete Alice's document.
    assert client.get(f"/api/documents/{alice_doc['id']}", headers=bearer(bob)).status_code == 404
    assert (
        client.delete(f"/api/documents/{alice_doc['id']}", headers=bearer(bob)).status_code == 404
    )
    assert [d["id"] for d in client.get("/api/documents", headers=bearer(bob)).json()] == [
        bob_doc["id"]
    ]

    # Vector store: searching as Alice for Bob's exact words returns only Alice's chunks.
    for query in ("quarterly budget invoices finance director", "photosynthesis"):
        assert {hit.document_id for hit in search(alice_id, query)} <= {uuid.UUID(alice_doc["id"])}
        assert {hit.document_id for hit in search(bob_id, query)} <= {uuid.UUID(bob_doc["id"])}
    assert search(bob_id, "budget")[0].document_id == uuid.UUID(bob_doc["id"])
    assert count(alice_id) == 1 and count(bob_id) == 1


def test_delete_removes_row_file_and_vectors(client: TestClient) -> None:
    token = signup_and_login(client, "a@example.com")
    other = signup_and_login(client, "b@example.com")
    _, document = upload(client, token, "plants.txt", ALICE_TEXT.encode())
    upload(client, other, "budget.txt", BOB_TEXT.encode())
    tenant = user_id(client, token)
    assert count(tenant) == 1

    assert (
        client.delete(f"/api/documents/{document['id']}", headers=bearer(token)).status_code == 204
    )
    assert client.get(f"/api/documents/{document['id']}", headers=bearer(token)).status_code == 404
    assert count(tenant) == 0
    assert count(user_id(client, other)) == 1  # the other user's data is untouched
    assert not (Path(get_settings().storage_dir) / str(tenant) / document["id"]).exists()
    # The same file can be uploaded again after deletion.
    assert upload(client, token, "plants.txt", ALICE_TEXT.encode())[0] == 202
