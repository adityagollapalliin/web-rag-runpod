import asyncio
from collections.abc import Awaitable

import httpx
from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the API process is up."""
    return {"status": "ok"}


async def _check(probe: Awaitable[object], limit_seconds: float) -> str:
    try:
        async with asyncio.timeout(limit_seconds):
            await probe
    except Exception as exc:  # any failure means "not ready", reported by type
        return f"error: {type(exc).__name__}"
    return "ok"


async def _ping_postgres(request: Request) -> None:
    async with request.app.state.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _ping_model(client: httpx.AsyncClient, base_url: str) -> None:
    (await client.get(f"{base_url.rstrip('/')}/health")).raise_for_status()


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, object]:
    """Readiness: every backing service answers."""
    timeout = get_settings().readiness_timeout_seconds
    state = request.app.state
    settings = get_settings()
    probes = {
        "postgres": _ping_postgres(request),
        "valkey": state.valkey.ping(),
        "qdrant": state.qdrant.get_collections(),
    }
    # Model servers are checked only when real ones are configured.
    model_urls = {
        "embedding_model": (settings.embedding_provider, settings.embedding_url),
        "rerank_model": (settings.rerank_provider, settings.rerank_url),
        "chat_model": (settings.chat_provider, settings.chat_url),
    }
    async with httpx.AsyncClient() as client:
        for name, (provider, url) in model_urls.items():
            if provider != "fake":
                probes[name] = _ping_model(client, url)
        results = await asyncio.gather(*(_check(probe, timeout) for probe in probes.values()))
    checks = dict(zip(probes, results, strict=True))
    ready = all(result == "ok" for result in results)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready else "unavailable", "checks": checks}
