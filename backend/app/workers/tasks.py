import asyncio
import logging
import uuid
from typing import Any

from app.services.parsing import IngestError
from app.workers.celery_app import celery_app
from app.workers.ingest import mark_failed, run_ingestion

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


@celery_app.task(bind=True, max_retries=MAX_RETRIES, name="docqa.ingest_document")
def ingest_document(self: Any, document_id: str) -> None:
    """Ingest one document. Bad files fail at once; infrastructure errors are retried."""
    doc_id = uuid.UUID(document_id)
    try:
        asyncio.run(run_ingestion(doc_id))
    except IngestError as exc:
        asyncio.run(mark_failed(doc_id, str(exc)))
    except Exception as exc:
        out_of_retries = self.request.retries >= MAX_RETRIES or self.request.is_eager
        if not out_of_retries:
            raise self.retry(exc=exc, countdown=5 * 2**self.request.retries) from exc
        logger.exception("Ingestion failed for document %s", document_id)
        asyncio.run(mark_failed(doc_id, "Internal error during ingestion"))
