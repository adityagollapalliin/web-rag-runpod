"""Late-interaction ("ColBERT-style") embedders: one vector per token instead of one per text.

Qdrant stores these as a multi-vector and scores a query against a passage by MaxSim: for
each query token take its best-matching passage token, then sum. It is used here to re-score
hybrid-search candidates inside the database, before the slower cross-encoder reranker.
"""

import hashlib
import math
import re
from typing import Protocol

import httpx

from app.core.config import Settings

MultiVector = list[list[float]]

_WORD = re.compile(r"[a-z0-9]+")


class MultiVectorEmbedder(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[MultiVector]: ...

    async def aclose(self) -> None: ...


class FakeMultiVectorEmbedder:
    """One deterministic unit vector per word, so MaxSim counts shared words. For tests."""

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[MultiVector]:
        return [self._embed_one(text) for text in texts]

    def _word_vector(self, word: str) -> list[float]:
        digest = hashlib.blake2b(word.encode(), digest_size=self.dimension).digest()
        vector = [byte / 127.5 - 1.0 for byte in digest]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def _embed_one(self, text: str) -> MultiVector:
        words = _WORD.findall(text.lower())[:512] or ["empty"]
        return [self._word_vector(word) for word in words]

    async def aclose(self) -> None:
        return None


class HttpMultiVectorEmbedder:
    """Calls vLLM's `/pooling` endpoint for a late-interaction model (token embeddings).

    Not yet exercised against a live server: confirm the request and response shape for the
    chosen model before enabling it in a deployment.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        dimension: int,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.dimension = dimension
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def embed(self, texts: list[str]) -> list[MultiVector]:
        response = await self._client.post(
            "/pooling", json={"model": self._model, "input": texts, "task": "token_embed"}
        )
        response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda row: row["index"])
        vectors: list[MultiVector] = [row["data"] for row in rows]
        if len(vectors) != len(texts) or any(
            not tokens or len(tokens[0]) != self.dimension for tokens in vectors
        ):
            raise RuntimeError("Multi-vector service returned an unexpected shape")
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()


def build_multivector_embedder(settings: Settings) -> MultiVectorEmbedder | None:
    if settings.multivector_provider == "none":
        return None
    if settings.multivector_provider == "fake":
        return FakeMultiVectorEmbedder(settings.multivector_dimension)
    return HttpMultiVectorEmbedder(
        settings.multivector_url, settings.multivector_model, settings.multivector_dimension
    )
