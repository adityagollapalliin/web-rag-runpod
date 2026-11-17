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


def _model_probes(client: httpx.AsyncClient) -> dict[str, Awaitable[None]]:
    """Health probes for the model servers; only real ones are checked, never the fakes."""
    settings = get_settings()
    model_urls = {
        "embedding_model": (settings.embedding_provider, settings.embedding_url),
        "rerank_model": (settings.rerank_provider, settings.rerank_url),
        "chat_model": (settings.chat_provider, settings.chat_url),
    }
    return {
        name: _ping_model(client, url)
        for name, (provider, url) in model_urls.items()
        if provider != "fake"
    }


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, object]:
    """Readiness: every backing service answers."""
    timeout = get_settings().readiness_timeout_seconds
    state = request.app.state
    probes: dict[str, Awaitable[object]] = {
        "postgres": _ping_postgres(request),
        "valkey": state.valkey.ping(),
        "qdrant": state.qdrant.get_collections(),
    }
    async with httpx.AsyncClient() as client:
        probes |= _model_probes(client)
        results = await asyncio.gather(*(_check(probe, timeout) for probe in probes.values()))
    checks = dict(zip(probes, results, strict=True))
    ready = all(result == "ok" for result in results)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready else "unavailable", "checks": checks}


@router.get("/api/status")
async def model_status() -> dict[str, object]:
    """Whether the model servers are up, for the web app to show while they load.

    After a pod (re)start the models take several minutes to load, one after another; until
    then questions cannot be answered.
    """
    timeout = get_settings().readiness_timeout_seconds
    async with httpx.AsyncClient() as client:
        probes = _model_probes(client)
        results = await asyncio.gather(*(_check(probe, timeout) for probe in probes.values()))
    loading = [name for name, result in zip(probes, results, strict=True) if result != "ok"]
    return {"models_ready": not loading, "loading": loading}
