import asyncio
import hashlib
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep, ValkeyDep
from app.core.config import get_settings
from app.models import Document, DocumentStatus
from app.schemas import DocumentOut
from app.services.parsing import detect_kind
from app.services.vector_store import VectorStore
from app.workers.ingest import corpus_version_key, document_path
from app.workers.tasks import ingest_document

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

_READ_CHUNK = 1024 * 1024


async def _save_upload(file: UploadFile, target: Path, max_bytes: int) -> tuple[int, str, bytes]:
    """Stream the upload to disk. Returns (size, sha256, first bytes)."""
    digest = hashlib.sha256()
    size = 0
    head = b""
    with target.open("wb") as out:
        while chunk := await file.read(_READ_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    f"File is larger than {max_bytes // (1024 * 1024)} MB",
                )
            head = head or chunk[:1024]
            digest.update(chunk)
            await asyncio.to_thread(out.write, chunk)
    return size, digest.hexdigest(), head


async def _owned_document(
    session: SessionDep, user: CurrentUser, document_id: uuid.UUID
) -> Document:
    document = (
        await session.execute(
            select(Document).where(Document.id == document_id, Document.user_id == user.id)
        )
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(file: UploadFile, user: CurrentUser, session: SessionDep) -> DocumentOut:
    """Store the file and queue ingestion. Poll `GET /api/documents/{id}` for progress."""
    settings = get_settings()
    document_id = uuid.uuid7()
    filename = Path(file.filename or "upload").name[:255]
    target = document_path(settings, user.id, document_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        size, sha256, head = await _save_upload(file, target, settings.max_upload_mb * 1024 * 1024)
        kind = detect_kind(filename, head)
        if size == 0 or kind is None:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "Upload a non-empty PDF, .txt or .md file",
            )
        session.add(
            Document(
                id=document_id,
                user_id=user.id,
                filename=filename,
                kind=kind,
                size_bytes=size,
                sha256=sha256,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT, "You have already uploaded this exact file"
            ) from None
    except HTTPException:
        target.unlink(missing_ok=True)
        raise

    document = await session.get_one(Document, document_id)
    try:
        # .delay() does blocking network I/O (or, in eager mode, runs the whole job).
        await asyncio.to_thread(ingest_document.delay, str(document_id))
    except Exception:
        logger.exception("Could not queue ingestion for document %s", document_id)
        document.status = DocumentStatus.FAILED
        document.error = "Could not queue the document for processing; delete it and try again"
        await session.commit()
    await session.refresh(document)
    return DocumentOut.model_validate(document)


@router.get("")
async def list_documents(user: CurrentUser, session: SessionDep) -> list[DocumentOut]:
    documents = (
        await session.execute(
            select(Document).where(Document.user_id == user.id).order_by(Document.id.desc())
        )
    ).scalars()
    return [DocumentOut.model_validate(document) for document in documents]


@router.get("/{document_id}")
async def get_document(
    document_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> DocumentOut:
    return DocumentOut.model_validate(await _owned_document(session, user, document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    valkey: ValkeyDep,
    request: Request,
) -> None:
    document = await _owned_document(session, user, document_id)
    store: VectorStore = request.app.state.vector_store
    # Vectors first: if this fails the row survives and the delete can be retried.
    await store.delete_document(user.id, document_id)
    await session.delete(document)
    await session.commit()
    document_path(get_settings(), user.id, document_id).unlink(missing_ok=True)
    await valkey.incr(corpus_version_key(user.id))
