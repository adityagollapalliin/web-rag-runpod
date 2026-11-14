"""The real model clients, exercised against canned HTTP responses (no model server needed)."""

import json
from collections.abc import Callable

import httpx
import pytest

from app.core.config import Settings
from app.services.chat import OpenAIChatModel
from app.services.embedding import OpenAICompatibleEmbedder
from app.services.rerank import HttpReranker


def _transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_embedder_orders_by_index_and_checks_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/embeddings"
        assert body == {"model": "m", "input": ["a", "b"]}
        rows = [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]
        return httpx.Response(200, json={"data": rows})

    embedder = OpenAICompatibleEmbedder("http://x/", "m", 2, transport=_transport(handler))
    assert await embedder.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]

    wrong_size = OpenAICompatibleEmbedder("http://x", "m", 3, transport=_transport(handler))
    with pytest.raises(RuntimeError):
        await wrong_size.embed(["a", "b"])


async def test_reranker_maps_scores_back_to_input_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/rerank"
        assert body == {"model": "r", "query": "q", "documents": ["a", "b", "c"]}
        results = [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.5},
            {"index": 1, "relevance_score": 0.1},
        ]
        return httpx.Response(200, json={"results": results})

    reranker = HttpReranker("http://x", "r", transport=_transport(handler))
    assert await reranker.rerank("q", ["a", "b", "c"]) == [0.5, 0.1, 0.9]
    assert await reranker.rerank("q", []) == []


async def test_chat_model_parses_the_event_stream() -> None:
    def chunk(delta: dict[str, str]) -> str:
        return "data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n"

    stream = (
        chunk({"role": "assistant"})
        + chunk({"content": "Hello"})
        + ": keep-alive comment\n\n"
        + chunk({"content": " world"})
        + 'data: {"choices": [], "usage": {"total_tokens": 5}}\n\n'
        + "data: [DONE]\n\n"
        + chunk({"content": "ignored after DONE"})
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["stream"] is True and body["model"] == "c" and body["max_tokens"] == 50
        return httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"})

    model = OpenAIChatModel("http://x", "c", 50, transport=_transport(handler))
    parts = [text async for text in model.stream([{"role": "user", "content": "hi"}])]
    assert parts == ["Hello", " world"]


async def test_chat_model_raises_on_server_error() -> None:
    model = OpenAIChatModel(
        "http://x", "c", 50, transport=_transport(lambda request: httpx.Response(503))
    )
    with pytest.raises(httpx.HTTPStatusError):
        _ = [text async for text in model.stream([{"role": "user", "content": "hi"}])]


def test_prod_settings_reject_fake_models_and_missing_chat_model() -> None:
    secret = "s" * 40
    with pytest.raises(ValueError, match="Fake model providers"):
        Settings(environment="prod", jwt_secret=secret)
    with pytest.raises(ValueError, match="DOCQA_CHAT_MODEL"):
        Settings(chat_provider="openai")
    Settings(
        environment="prod",
        jwt_secret=secret,
        embedding_provider="openai",
        rerank_provider="http",
        chat_provider="openai",
        chat_model="some/model",
    )
