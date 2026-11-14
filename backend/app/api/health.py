import asyncio
from collections.abc import Awaitable

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


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, object]:
    """Readiness: every backing service answers."""
    timeout = get_settings().readiness_timeout_seconds
    state = request.app.state
    names = ("postgres", "valkey", "qdrant")
    results = await asyncio.gather(
        _check(_ping_postgres(request), timeout),
        _check(state.valkey.ping(), timeout),
        _check(state.qdrant.get_collections(), timeout),
    )
    checks = dict(zip(names, results, strict=True))
    ready = all(result == "ok" for result in results)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready else "unavailable", "checks": checks}
