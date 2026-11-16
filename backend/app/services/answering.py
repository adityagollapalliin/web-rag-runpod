"""Retrieval for a question: cached query embedding -> hybrid search -> rerank -> prompt."""

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from valkey.asyncio import Valkey

from app.core.config import Settings
from app.services.chat import Messages
from app.services.embedding import Embedder
from app.services.rerank import Reranker
from app.services.vector_store import VectorStore

NO_RESULTS_ANSWER = (
    "I couldn't find anything in your documents about that. "
    "Try rephrasing, or upload a document that covers it."
)

SYSTEM_PROMPT = (
    "You answer questions using only the numbered sources provided. "
    "Cite the sources you rely on inline as [1], [2] and so on. "
    "If the sources do not contain the answer, say so plainly instead of guessing. "
    "Be concise."
)

_SNIPPET_CHARS = 300
_QUERY_EMBEDDING_TTL = 24 * 3600


@dataclass(frozen=True)
class Source:
    n: int  # citation number shown to the user, starting at 1
    document_id: str
    filename: str
    page_start: int
    page_end: int
    snippet: str
    text: str
    score: float

    def public(self) -> dict[str, Any]:
        """What the client and the stored message get (no full chunk text)."""
        data = asdict(self)
        del data["text"]
        return data


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def answer_cache_key(user_id: uuid.UUID, corpus_version: str, settings: Settings, q: str) -> str:
    """Changes when the user's documents change (corpus version) or the models change."""
    normalized = " ".join(q.lower().split())
    models = _sha(f"{settings.embedding_model}|{settings.rerank_model}|{settings.chat_model}")[:12]
    return f"ans:{user_id}:{corpus_version}:{models}:{_sha(normalized)}"


async def embed_query(
    valkey: Valkey, embedder: Embedder, settings: Settings, question: str
) -> list[float]:
    text = settings.embedding_query_prefix + question
    key = f"emb:{settings.embedding_provider}:{settings.embedding_model}:{_sha(text)}"
    if cached := await valkey.get(key):
        vector: list[float] = json.loads(cached)
        if len(vector) == embedder.dimension:
            return vector
    vector = (await embedder.embed([text]))[0]
    await valkey.set(key, json.dumps(vector), ex=_QUERY_EMBEDDING_TTL)
    return vector


async def retrieve(
    *,
    user_id: uuid.UUID,
    question: str,
    valkey: Valkey,
    store: VectorStore,
    embedder: Embedder,
    reranker: Reranker,
    settings: Settings,
) -> list[Source]:
    """The best few chunks for this question from this user's documents, best first."""
    vector = await embed_query(valkey, embedder, settings, question)
    hits = await store.hybrid_search(user_id, question, vector, limit=settings.retrieve_limit)
    if not hits:
        return []
    scores = await reranker.rerank(question, [hit.text for hit in hits])
    ranked = sorted(zip(scores, hits, strict=True), key=lambda pair: pair[0], reverse=True)
    return [
        Source(
            n=n,
            document_id=str(hit.document_id),
            filename=hit.filename,
            page_start=hit.page_start,
            page_end=hit.page_end,
            snippet=hit.text[:_SNIPPET_CHARS],
            text=hit.text,
            score=round(score, 4),
        )
        for n, (score, hit) in enumerate(ranked[: settings.context_chunks], start=1)
    ]


def _pages(source: Source) -> str:
    if source.page_start == source.page_end:
        return f"page {source.page_start}"
    return f"pages {source.page_start}-{source.page_end}"


def build_messages(question: str, sources: list[Source], history: Messages) -> Messages:
    blocks = "\n\n".join(f"[{s.n}] {s.filename} ({_pages(s)})\n{s.text}" for s in sources)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": f"Sources:\n{blocks}\n\nQuestion: {question}"},
    ]
