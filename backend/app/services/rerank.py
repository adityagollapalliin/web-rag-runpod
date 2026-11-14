"""Rerankers score (question, passage) pairs: a real HTTP client and a deterministic fake."""

import re
from typing import Protocol

import httpx

from app.core.config import Settings

_WORD = re.compile(r"[a-z0-9]+")


class Reranker(Protocol):
    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        """One relevance score per text, in the same order; higher is more relevant."""
        ...

    async def aclose(self) -> None: ...


class FakeReranker:
    """Scores by the share of query words present in the passage. For tests and local runs."""

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        query_words = set(_WORD.findall(query.lower()))
        if not query_words:
            return [0.0] * len(texts)
        return [
            len(query_words & set(_WORD.findall(text.lower()))) / len(query_words) for text in texts
        ]

    async def aclose(self) -> None:
        return None


class HttpReranker:
    """Calls a Cohere/Jina-style `/v1/rerank` endpoint (served by vLLM on Runpod)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        response = await self._client.post(
            "/v1/rerank", json={"model": self._model, "query": query, "documents": texts}
        )
        response.raise_for_status()
        scores = [0.0] * len(texts)
        results = response.json()["results"]
        if len(results) != len(texts):
            raise RuntimeError("Rerank service returned an unexpected number of results")
        for result in results:
            scores[result["index"]] = float(result["relevance_score"])
        return scores

    async def aclose(self) -> None:
        await self._client.aclose()


def build_reranker(settings: Settings) -> Reranker:
    if settings.rerank_provider == "fake":
        return FakeReranker()
    return HttpReranker(settings.rerank_url, settings.rerank_model)
