"""Embedding models behind one interface: a real HTTP client and a deterministic fake."""

import hashlib
import math
import re
from typing import Protocol

import httpx

from app.core.config import Settings

_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


class FakeEmbedder:
    """Hashed bag-of-words vectors: no model, but texts sharing words score as similar.

    For tests and development on a machine without a GPU. Not for real retrieval quality.
    """

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for word in _WORD.findall(text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            slot = int.from_bytes(digest[:4]) % self.dimension
            vector[slot] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]

    async def aclose(self) -> None:
        return None


class OpenAICompatibleEmbedder:
    """Calls an OpenAI-style `/v1/embeddings` endpoint (vLLM on Runpod)."""

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

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.post(
            "/v1/embeddings", json={"model": self._model, "input": texts}
        )
        response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda row: row["index"])
        vectors = [row["embedding"] for row in rows]
        if len(vectors) != len(texts) or any(len(v) != self.dimension for v in vectors):
            raise RuntimeError("Embedding service returned an unexpected shape")
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "fake":
        return FakeEmbedder(settings.embedding_dimension)
    return OpenAICompatibleEmbedder(
        settings.embedding_url, settings.embedding_model, settings.embedding_dimension
    )
