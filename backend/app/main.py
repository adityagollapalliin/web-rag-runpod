from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import valkey.asyncio as valkey
from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import api_keys, auth, documents, health
from app.core.config import get_settings
from app.services.vector_store import VectorStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    app.state.engine = engine
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    app.state.valkey = valkey.from_url(  # type: ignore[no-untyped-call]
        settings.valkey_url, decode_responses=True
    )
    # No version probe at startup: /readyz is what reports whether Qdrant is reachable.
    app.state.qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    app.state.vector_store = VectorStore(
        app.state.qdrant, settings.qdrant_collection, settings.embedding_dimension
    )
    try:
        yield
    finally:
        await app.state.qdrant.close()
        await app.state.valkey.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="DocQA", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(auth.me_router)
    app.include_router(api_keys.router)
    app.include_router(documents.router)
    return app


app = create_app()
