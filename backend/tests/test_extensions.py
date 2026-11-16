"""Phase 7: retrieval strategies, multi-vectors, relevance feedback, evaluation, ratings."""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from app.core.config import Settings, get_settings
from app.evaluate import evaluate
from app.services.chunking import Chunk
from app.services.embedding import FakeEmbedder
from app.services.multivector import FakeMultiVectorEmbedder, HttpMultiVectorEmbedder
from app.services.vector_store import VectorStore
from tests.conftest import bearer, signup_and_login

TEXTS = [
    "The zygomorphic orchid blooms at midnight in the greenhouse.",
    "Quarterly invoices are approved by the finance director.",
    "Orchids need indirect light and weekly watering.",
    "The ferry sails six times a day in summer.",
]


def run_with_store[T](
    action: Callable[[VectorStore, FakeEmbedder, FakeMultiVectorEmbedder], Awaitable[T]],
    multivectors: bool = False,
) -> T:
    async def run() -> T:
        settings = get_settings()
        qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
        try:
            store = VectorStore(
                qdrant, settings.qdrant_collection, 64, 16 if multivectors else None
            )
            return await action(store, FakeEmbedder(64), FakeMultiVectorEmbedder(16))
        finally:
            await qdrant.close()

    return asyncio.run(run())


async def index(
    store: VectorStore,
    embedder: FakeEmbedder,
    multi: FakeMultiVectorEmbedder,
    tenant: uuid.UUID,
    texts: list[str] = TEXTS,
) -> None:
    chunks = [Chunk(index=i, text=text, page_start=1, page_end=1) for i, text in enumerate(texts)]
    await store.ensure_collection()
    await store.replace_document(
        tenant,
        uuid.uuid4(),
        "notes.txt",
        chunks,
        await embedder.embed(texts),
        await multi.embed(texts) if store.has_multivectors else None,
    )


def test_dense_only_and_hybrid_search_both_find_the_passage() -> None:
    tenant = uuid.uuid4()

    async def action(store: VectorStore, embedder: FakeEmbedder, multi: Any) -> None:
        await index(store, embedder, multi, tenant)
        vector = (await embedder.embed(["zygomorphic orchid"]))[0]
        dense = await store.search(tenant, "zygomorphic orchid", vector, use_text=False)
        hybrid = await store.search(tenant, "zygomorphic orchid", vector)
        assert "zygomorphic" in dense[0].text and "zygomorphic" in hybrid[0].text
        assert len(dense) == 4  # dense search returns every chunk, however distant
        assert all(hit.point_id for hit in hybrid)
        # A word no chunk contains gets no BM25 candidates, but dense still answers.
        nothing = (await embedder.embed(["xylophone"]))[0]
        assert len(await store.search(tenant, "xylophone", nothing)) == 4

    run_with_store(action)


def test_multivector_rescoring_ranks_by_shared_words_and_stays_in_tenant() -> None:
    tenant, other = uuid.uuid4(), uuid.uuid4()

    async def action(
        store: VectorStore, embedder: FakeEmbedder, multi: FakeMultiVectorEmbedder
    ) -> None:
        await index(store, embedder, multi, tenant)
        await index(store, embedder, multi, other, ["Invoices approved by the finance director."])
        question = "who approves quarterly invoices"
        hits = await store.search(
            tenant,
            question,
            (await embedder.embed([question]))[0],
            query_multivector=(await multi.embed([question]))[0],
            limit=2,
        )
        assert len(hits) == 2 and "Quarterly invoices" in hits[0].text
        assert hits[0].score > hits[1].score
        assert all(hit.filename == "notes.txt" for hit in hits)
        assert await store.count(tenant) == 4 and await store.count(other) == 1

    run_with_store(action, multivectors=True)


def test_enabling_multivectors_on_an_old_collection_is_refused_clearly() -> None:
    tenant = uuid.uuid4()

    async def plain(store: VectorStore, embedder: FakeEmbedder, multi: Any) -> None:
        await index(store, embedder, multi, tenant)

    async def upgraded(store: VectorStore, embedder: Any, multi: Any) -> None:
        with pytest.raises(RuntimeError, match="DOCQA_QDRANT_COLLECTION"):
            await store.ensure_collection()

    run_with_store(plain)
    run_with_store(upgraded, multivectors=True)


def test_multivector_collection_requires_multivectors_on_write() -> None:
    async def action(store: VectorStore, embedder: FakeEmbedder, multi: Any) -> None:
        await store.ensure_collection()
        chunk = Chunk(index=0, text="a", page_start=1, page_end=1)
        with pytest.raises(ValueError, match="multi-vector"):
            await store.replace_document(
                uuid.uuid4(), uuid.uuid4(), "a.txt", [chunk], await embedder.embed(["a"])
            )

    run_with_store(action, multivectors=True)


def test_feedback_search_returns_other_points_from_the_same_tenant() -> None:
    tenant, other = uuid.uuid4(), uuid.uuid4()

    async def action(store: VectorStore, embedder: FakeEmbedder, multi: Any) -> None:
        await index(store, embedder, multi, tenant)
        await index(store, embedder, multi, other, ["Orchids from another account."])
        vector = (await embedder.embed(["orchid care"]))[0]
        first = await store.search(tenant, "orchid care", vector)
        judged = [(first[0].point_id, 0.9), (first[1].point_id, 0.1)]
        more = await store.feedback_search(tenant, vector, judged, (0.12, 0.43, 0.03))
        ids = {hit.point_id for hit in more}
        assert ids and ids.isdisjoint({point for point, _ in judged})  # judged ones excluded
        assert ids <= {hit.point_id for hit in first}  # nothing from the other tenant

    run_with_store(action)


@pytest.mark.parametrize("multivector", ["none", "fake"])
def test_evaluation_reports_every_strategy_and_cleans_up(multivector: str) -> None:
    dataset = json.loads((Path(__file__).parent.parent / "eval" / "sample.json").read_text())
    settings = get_settings().model_copy(
        update={"multivector_provider": multivector, "multivector_dimension": 16}
    )
    results = asyncio.run(evaluate(dataset, settings))

    expected = 6 if multivector == "fake" else 4
    assert len(results) == expected and "hybrid + rerank + feedback" in results
    for metrics in results.values():
        assert 0 <= metrics["hit@1"] <= metrics["hit@k"] <= 1
        assert metrics["hit@1"] <= metrics["mrr"] <= metrics["hit@k"]
    # Keyword search can only add candidates to dense search.
    assert results["hybrid (dense + BM25)"]["hit@k"] >= results["dense only"]["hit@k"] > 0.5

    async def leftovers() -> list[str]:
        qdrant = AsyncQdrantClient(url=get_settings().qdrant_url, check_compatibility=False)
        names = [c.name for c in (await qdrant.get_collections()).collections]
        await qdrant.close()
        return [name for name in names if name.startswith("eval_")]

    assert asyncio.run(leftovers()) == []


def test_ask_works_with_multivectors_and_feedback_switched_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in {
        "multivector_provider": "fake",
        "multivector_dimension": 16,
        "feedback_retrieval": True,
        "chunk_max_chars": 80,
        "chunk_overlap_chars": 0,
    }.items():
        monkeypatch.setattr(get_settings(), name, value)
    with TestClient(client.app) as fresh:  # rebuild app state with the new settings
        token = signup_and_login(fresh, "a@example.com")
        text = " ".join(TEXTS)
        uploaded = fresh.post(
            "/api/documents", files={"file": ("notes.txt", text.encode())}, headers=bearer(token)
        ).json()
        assert uploaded["status"] == "ready" and uploaded["chunk_count"] >= 3, uploaded
        response = fresh.post(
            "/api/ask",
            json={"question": "When does the zygomorphic orchid bloom?"},
            headers=bearer(token),
        )
        assert (
            "event: done" in response.text
            and "zygomorphic orchid blooms at midnight" in response.text
        )


def test_rating_an_answer(client: TestClient) -> None:
    alice = signup_and_login(client, "a@example.com")
    bob = signup_and_login(client, "b@example.com")
    client.post(
        "/api/documents", files={"file": ("a.txt", TEXTS[0].encode())}, headers=bearer(alice)
    )
    stream = client.post("/api/ask", json={"question": "orchid?"}, headers=bearer(alice)).text
    done = json.loads(stream.strip().split("\n\n")[-1].split("data: ", 1)[1])
    url = f"/api/messages/{done['message_id']}/rating"

    assert client.put(url, json={"rating": 1}, headers=bearer(alice)).json()["rating"] == 1
    detail = client.get(
        f"/api/conversations/{done['conversation_id']}", headers=bearer(alice)
    ).json()
    assert [m["rating"] for m in detail["messages"]] == [None, 1]
    assert client.put(url, json={"rating": -1}, headers=bearer(alice)).json()["rating"] == -1
    assert client.put(url, json={"rating": 0}, headers=bearer(alice)).json()["rating"] is None

    assert client.put(url, json={"rating": 5}, headers=bearer(alice)).status_code == 422
    assert client.put(url, json={"rating": 1}, headers=bearer(bob)).status_code == 404
    assert client.put(url, json={"rating": 1}).status_code == 401
    question_id = detail["messages"][0]["id"]  # only answers can be rated
    response = client.put(
        f"/api/messages/{question_id}/rating", json={"rating": 1}, headers=bearer(alice)
    )
    assert response.status_code == 404


async def test_http_multivector_client_checks_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/pooling" and body["input"] == ["a b", "c"]
        rows = [{"index": 1, "data": [[0.0, 1.0]]}, {"index": 0, "data": [[1.0, 0.0], [0.5, 0.5]]}]
        return httpx.Response(200, json={"data": rows})

    good = HttpMultiVectorEmbedder("http://x", "m", 2, transport=httpx.MockTransport(handler))
    assert await good.embed(["a b", "c"]) == [[[1.0, 0.0], [0.5, 0.5]], [[0.0, 1.0]]]
    wrong = HttpMultiVectorEmbedder("http://x", "m", 3, transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        await wrong.embed(["a b", "c"])


def test_multivector_settings_validation() -> None:
    with pytest.raises(ValueError, match="DOCQA_MULTIVECTOR_MODEL"):
        Settings(multivector_provider="http")
    with pytest.raises(ValueError, match="Fake model providers"):
        Settings(
            environment="prod",
            jwt_secret="s" * 40,
            embedding_provider="openai",
            rerank_provider="http",
            chat_provider="openai",
            chat_model="m",
            multivector_provider="fake",
        )
