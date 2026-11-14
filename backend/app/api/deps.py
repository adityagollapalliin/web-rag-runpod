from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from valkey.asyncio import Valkey

from app.core import ratelimit
from app.core.config import get_settings
from app.core.security import API_KEY_PREFIX, decode_access_token, hash_token
from app.models import ApiKey, User

_LAST_USED_RESOLUTION = timedelta(minutes=1)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_valkey(request: Request) -> Valkey:
    return request.app.state.valkey  # type: ignore[no-any-return]


SessionDep = Annotated[AsyncSession, Depends(get_session)]
ValkeyDep = Annotated[Valkey, Depends(get_valkey)]


def client_ip(request: Request) -> str:
    header = get_settings().client_ip_header
    if header and (value := request.headers.get(header)):
        return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _too_many_requests() -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests", headers={"Retry-After": "60"}
    )


def ip_rate_limit(scope: str) -> Callable[[Request, Valkey], Awaitable[None]]:
    """Dependency limiting unauthenticated endpoints per client IP."""

    async def dependency(request: Request, valkey: ValkeyDep) -> None:
        limit = get_settings().auth_rate_limit_per_minute
        if not await ratelimit.allow(valkey, f"{scope}:{client_ip(request)}", limit):
            raise _too_many_requests()

    return dependency


async def _user_from_api_key(session: AsyncSession, key: str) -> User | None:
    if not key.startswith(API_KEY_PREFIX):
        return None
    row = (
        await session.execute(
            select(ApiKey, User)
            .join(User, User.id == ApiKey.user_id)
            .where(ApiKey.key_hash == hash_token(key), ApiKey.revoked_at.is_(None))
        )
    ).one_or_none()
    if row is None:
        return None
    api_key, user = row
    now = datetime.now(UTC)
    if api_key.last_used_at is None or now - api_key.last_used_at > _LAST_USED_RESOLUTION:
        api_key.last_used_at = now
        await session.commit()
    return user


async def current_user(
    session: SessionDep,
    valkey: ValkeyDep,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> User:
    """Accepts `Authorization: Bearer <access token>` or `X-API-Key: dq_...`."""
    user: User | None = None
    if x_api_key:
        user = await _user_from_api_key(session, x_api_key)
    elif authorization and authorization.lower().startswith("bearer "):
        user_id = decode_access_token(authorization[7:].strip())
        if user_id is not None:
            user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    limit = get_settings().api_rate_limit_per_minute
    if not await ratelimit.allow(valkey, f"user:{user.id}", limit):
        raise _too_many_requests()
    return user


CurrentUser = Annotated[User, Depends(current_user)]
