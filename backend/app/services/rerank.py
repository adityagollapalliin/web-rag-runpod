"""Rerankers score (question, passage) pairs: a real HTTP client and a deterministic fake."""

import re
from typing import Literal, Protocol

import httpx

from app.core.config import Settings

_WORD = re.compile(r"[a-z0-9]+")

# The Qwen3 rerankers are generative models scored on whether they answer "yes"; they only
# rank well when the pair is laid out as in training (see the model card and vLLM's example).
_QWEN3_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query "
    'and the Instruct provided. Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n<|im_start|>user\n"
)
_QWEN3_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_QWEN3_INSTRUCTION = "Given a question, retrieve passages that answer it"

RerankPrompt = Literal["none", "qwen3"]


def format_pair(prompt: RerankPrompt, query: str, texts: list[str]) -> tuple[str, list[str]]:
    """Wrap a query and its passages in the layout the reranker model expects."""
    if prompt == "qwen3":
        return (
            f"{_QWEN3_PREFIX}<Instruct>: {_QWEN3_INSTRUCTION}\n<Query>: {query}\n",
            [f"<Document>: {text}{_QWEN3_SUFFIX}" for text in texts],
        )
    return query, texts


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
        prompt: RerankPrompt = "none",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._prompt: RerankPrompt = prompt
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        query, documents = format_pair(self._prompt, query, texts)
        response = await self._client.post(
            "/v1/rerank", json={"model": self._model, "query": query, "documents": documents}
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
    return HttpReranker(settings.rerank_url, settings.rerank_model, settings.rerank_prompt)
