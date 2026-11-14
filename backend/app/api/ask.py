import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, ValkeyDep, too_many_requests
from app.core import ratelimit
from app.core.config import get_settings
from app.models import Conversation, Message
from app.schemas import AskIn, ConversationDetailOut, ConversationOut, MessageOut
from app.services import answering
from app.services.chat import Messages
from app.workers.ingest import corpus_version_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["ask"])

_HISTORY_MESSAGES = 6


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _owned_conversation(
    session: SessionDep, user: CurrentUser, conversation_id: uuid.UUID
) -> Conversation:
    conversation = (
        await session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.post(
    "/ask",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def ask(
    body: AskIn, user: CurrentUser, session: SessionDep, valkey: ValkeyDep, request: Request
) -> StreamingResponse:
    """Answer a question from the caller's documents, streamed as server-sent events.

    Events, in order: `sources` (the numbered passages), `token` (answer text, repeated),
    then `done` (ids and whether the answer came from cache). `error` replaces `done` on failure.
    """
    settings = get_settings()
    if not await ratelimit.allow(valkey, f"ask:{user.id}", settings.ask_rate_limit_per_minute):
        raise too_many_requests()

    user_id = user.id
    question = body.question.strip()
    history: Messages = []
    if body.conversation_id is not None:
        await _owned_conversation(session, user, body.conversation_id)
        recent = (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == body.conversation_id)
                .order_by(Message.id.desc())
                .limit(_HISTORY_MESSAGES)
            )
        ).scalars()
        history = [{"role": m.role, "content": m.content} for m in reversed(list(recent))]
    conversation_id = body.conversation_id or uuid.uuid7()
    state = request.app.state

    async def persist(answer: str, citations: list[dict[str, Any]]) -> uuid.UUID:
        # Own session: the request-scoped one may be closed before the stream finishes.
        async with state.sessionmaker() as db:
            if body.conversation_id is None:
                db.add(Conversation(id=conversation_id, user_id=user_id, title=question[:120]))
                await db.flush()
            # Explicit ids keep the question ordered before its answer.
            db.add(
                Message(
                    id=uuid.uuid7(), conversation_id=conversation_id, role="user", content=question
                )
            )
            reply = Message(
                id=uuid.uuid7(),
                conversation_id=conversation_id,
                role="assistant",
                content=answer,
                citations=citations,
            )
            db.add(reply)
            await db.commit()
            return reply.id

    async def events() -> AsyncIterator[str]:
        try:
            # Only stand-alone questions are cached: a follow-up depends on what came before.
            cache_key = None
            if not history:
                version = await valkey.get(corpus_version_key(user_id)) or "0"
                cache_key = answering.answer_cache_key(user_id, version, settings, question)
                if cached := await valkey.get(cache_key):
                    hit = json.loads(cached)
                    yield _sse("sources", {"sources": hit["sources"]})
                    yield _sse("token", {"text": hit["answer"]})
                    message_id = await persist(hit["answer"], hit["sources"])
                    yield _sse("done", _done(conversation_id, message_id, cached=True))
                    return

            sources = await answering.retrieve(
                user_id=user_id,
                question=question,
                valkey=valkey,
                store=state.vector_store,
                embedder=state.embedder,
                reranker=state.reranker,
                settings=settings,
            )
            public_sources = [source.public() for source in sources]
            yield _sse("sources", {"sources": public_sources})

            if not sources:
                answer = answering.NO_RESULTS_ANSWER
                yield _sse("token", {"text": answer})
            else:
                parts: list[str] = []
                messages = answering.build_messages(question, sources, history)
                async for text in state.chat_model.stream(messages):
                    parts.append(text)
                    yield _sse("token", {"text": text})
                answer = "".join(parts).strip()

            message_id = await persist(answer, public_sources)
            if cache_key is not None:
                await valkey.set(
                    cache_key,
                    json.dumps({"answer": answer, "sources": public_sources}),
                    ex=settings.answer_cache_seconds,
                )
            yield _sse("done", _done(conversation_id, message_id, cached=False))
        except Exception:
            logger.exception("Answering failed for user %s", user_id)
            yield _sse("error", {"message": "Something went wrong while answering. Try again."})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # Stop proxies from buffering, so tokens reach the browser as they are produced.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _done(conversation_id: uuid.UUID, message_id: uuid.UUID, *, cached: bool) -> dict[str, Any]:
    return {
        "conversation_id": str(conversation_id),
        "message_id": str(message_id),
        "cached": cached,
    }


@router.get("/conversations")
async def list_conversations(user: CurrentUser, session: SessionDep) -> list[ConversationOut]:
    conversations = (
        await session.execute(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.id.desc())
            .limit(200)
        )
    ).scalars()
    return [ConversationOut.model_validate(c) for c in conversations]


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ConversationDetailOut:
    conversation = await _owned_conversation(session, user, conversation_id)
    messages = (
        await session.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        )
    ).scalars()
    return ConversationDetailOut(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in messages],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> None:
    await session.delete(await _owned_conversation(session, user, conversation_id))
    await session.commit()
