import asyncio
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SessionDep, ip_rate_limit
from app.core.config import get_settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    verify_password,
)
from app.models import RefreshToken, User
from app.schemas import LoginIn, SignupIn, TokenOut, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE = "docqa_refresh"
_COOKIE_PATH = "/api/auth"


def _token_response(user_id: uuid.UUID) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user_id),
        expires_in=get_settings().access_token_minutes * 60,
    )


def _issue_refresh_token(session: AsyncSession, user_id: uuid.UUID, response: Response) -> None:
    """Add a new refresh token row (caller commits) and set it as an httpOnly cookie."""
    settings = get_settings()
    lifetime = timedelta(days=settings.refresh_token_days)
    raw = new_opaque_token()
    session.add(
        RefreshToken(
            user_id=user_id, token_hash=hash_token(raw), expires_at=datetime.now(UTC) + lifetime
        )
    )
    response.set_cookie(
        REFRESH_COOKIE,
        raw,
        max_age=int(lifetime.total_seconds()),
        path=_COOKIE_PATH,
        httponly=True,
        secure=settings.is_prod,
        samesite="lax",
    )


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(ip_rate_limit("signup"))],
)
async def signup(body: SignupIn, session: SessionDep) -> UserOut:
    expected = get_settings().invite_code
    if not expected or not hmac.compare_digest(body.invite_code.encode(), expected.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid invite code")
    password_hash = await asyncio.to_thread(hash_password, body.password)
    user = User(email=body.email.lower(), password_hash=password_hash)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.post("/login", dependencies=[Depends(ip_rate_limit("login"))])
async def login(body: LoginIn, session: SessionDep, response: Response) -> TokenOut:
    user = (
        await session.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    stored_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    password_ok = await asyncio.to_thread(verify_password, stored_hash, body.password)
    if user is None or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    _issue_refresh_token(session, user.id, response)
    await session.commit()
    return _token_response(user.id)


@router.post("/refresh", dependencies=[Depends(ip_rate_limit("refresh"))])
async def refresh(
    session: SessionDep,
    response: Response,
    docqa_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenOut:
    """Rotate the refresh token. Reuse of an already-rotated token signs the user out everywhere."""
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if not docqa_refresh:
        raise invalid
    token = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_token(docqa_refresh))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if token is None:
        raise invalid
    now = datetime.now(UTC)
    if token.revoked_at is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == token.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.commit()
        raise invalid
    if token.expires_at <= now:
        raise invalid
    token.revoked_at = now
    _issue_refresh_token(session, token.user_id, response)
    await session.commit()
    return _token_response(token.user_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    session: SessionDep,
    response: Response,
    docqa_refresh: Annotated[str | None, Cookie()] = None,
) -> None:
    if docqa_refresh:
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == hash_token(docqa_refresh),
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
    response.delete_cookie(REFRESH_COOKIE, path=_COOKIE_PATH)


me_router = APIRouter(prefix="/api", tags=["auth"])


@me_router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
