"""The ingestion pipeline: file on disk -> pages -> chunks -> vectors -> Qdrant."""

import asyncio
import uuid
from pathlib import Path

import valkey.asyncio as valkey
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.models import Document, DocumentStatus
from app.services.chunking import chunk_pages
from app.services.embedding import Embedder, build_embedder
from app.services.parsing import IngestError, extract_pages
from app.services.vector_store import VectorStore

_EMBED_BATCH = 32


def document_path(settings: Settings, user_id: uuid.UUID, document_id: uuid.UUID) -> Path:
    return settings.storage_dir / str(user_id) / str(document_id)


def corpus_version_key(user_id: uuid.UUID) -> str:
    """Bumped whenever a user's searchable documents change; answer caches key on it."""
    return f"corpus:{user_id}"


async def _embed_all(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH):
        vectors.extend(await embedder.embed(texts[start : start + _EMBED_BATCH]))
    return vectors


async def _ingest(
    session: AsyncSession, store: VectorStore, embedder: Embedder, document_id: uuid.UUID
) -> uuid.UUID | None:
    """Returns the owner's id once the document is searchable, or None if it no longer exists."""
    settings = get_settings()
    document = await session.get(Document, document_id)
    if document is None:
        return None
    user_id, filename, kind = document.user_id, document.filename, document.kind
    document.status, document.error = DocumentStatus.PROCESSING, None
    await session.commit()

    path = document_path(settings, user_id, document_id)
    pages = await asyncio.to_thread(extract_pages, path, kind)
    chunks = chunk_pages(pages, settings.chunk_max_chars, settings.chunk_overlap_chars)
    if not chunks:
        raise IngestError("No text found (a scanned PDF needs OCR, which is not supported yet)")
    vectors = await _embed_all(embedder, [chunk.text for chunk in chunks])

    await store.ensure_collection()
    await store.replace_document(user_id, document_id, filename, chunks, vectors)

    # The user may have deleted the document while it was being processed.
    document = await session.get(Document, document_id, populate_existing=True)
    if document is None:
        await store.delete_document(user_id, document_id)
        return None
    document.status = DocumentStatus.READY
    document.page_count, document.chunk_count = len(pages), len(chunks)
    await session.commit()
    return user_id


async def run_ingestion(document_id: uuid.UUID) -> None:
    """Entry point for the worker: builds its own connections, since each job has its own loop."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    cache = valkey.from_url(settings.valkey_url)  # type: ignore[no-untyped-call]
    try:
        store = VectorStore(qdrant, settings.qdrant_collection, settings.embedding_dimension)
        embedder = build_embedder(settings)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                user_id = await _ingest(session, store, embedder, document_id)
        finally:
            await embedder.aclose()
        if user_id is not None:
            await cache.incr(corpus_version_key(user_id))
    finally:
        await cache.aclose()
        await qdrant.close()
        await engine.dispose()


async def mark_failed(document_id: uuid.UUID, reason: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as session:
            document = await session.get(Document, document_id)
            if document is not None:
                document.status, document.error = DocumentStatus.FAILED, reason[:500]
                await session.commit()
    finally:
        await engine.dispose()
