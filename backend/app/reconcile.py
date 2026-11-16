"""Bring the database and the vector store back in step. Run once at startup.

On Runpod the databases are restored from separate backups, so the document table can be
newer than the vector snapshot. Any document that claims to be searchable but has no vectors,
or that was mid-ingestion when the pod stopped, is queued again. The original files are on
persistent storage, so nothing is lost; it is simply processed once more.

Usage: python -m app.reconcile
"""

import asyncio
import logging
import uuid

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models import Document, DocumentStatus
from app.services.vector_store import VectorStore
from app.workers.tasks import ingest_document

logger = logging.getLogger(__name__)


async def find_and_requeue() -> list[uuid.UUID]:
    """Mark out-of-step documents as queued and return their ids."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    store = VectorStore(qdrant, settings.qdrant_collection, settings.embedding_dimension)
    requeue: list[uuid.UUID] = []
    try:
        async with async_sessionmaker(engine)() as session:
            documents = (
                await session.execute(
                    select(Document).where(
                        Document.status.in_([DocumentStatus.READY, DocumentStatus.PROCESSING])
                    )
                )
            ).scalars()
            for document in documents:
                interrupted = document.status == DocumentStatus.PROCESSING
                if interrupted or await store.count(document.user_id, document.id) == 0:
                    document.status = DocumentStatus.QUEUED
                    requeue.append(document.id)
            await session.commit()
    finally:
        await qdrant.close()
        await engine.dispose()
    return requeue


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[reconcile] %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    document_ids = asyncio.run(find_and_requeue())
    for document_id in document_ids:
        ingest_document.delay(str(document_id))
    logger.info("re-queued %d document(s)", len(document_ids))


if __name__ == "__main__":
    main()
