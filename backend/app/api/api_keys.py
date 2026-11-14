import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.security import hash_token, new_api_key
from app.models import ApiKey
from app.schemas import ApiKeyCreatedOut, ApiKeyCreateIn, ApiKeyOut

router = APIRouter(prefix="/api/api-keys", tags=["api keys"])

MAX_ACTIVE_KEYS = 20


@router.get("")
async def list_api_keys(user: CurrentUser, session: SessionDep) -> list[ApiKeyOut]:
    keys = (
        await session.execute(
            select(ApiKey)
            .where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
            .order_by(ApiKey.created_at.desc())
        )
    ).scalars()
    return [ApiKeyOut.model_validate(key) for key in keys]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreateIn, user: CurrentUser, session: SessionDep
) -> ApiKeyCreatedOut:
    active = (
        await session.execute(
            select(ApiKey.id).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
        )
    ).all()
    if len(active) >= MAX_ACTIVE_KEYS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Limit of {MAX_ACTIVE_KEYS} keys reached")
    key, prefix = new_api_key()
    api_key = ApiKey(user_id=user.id, name=body.name, prefix=prefix, key_hash=hash_token(key))
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return ApiKeyCreatedOut(**ApiKeyOut.model_validate(api_key).model_dump(), key=key)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> None:
    api_key = (
        await session.execute(
            select(ApiKey).where(
                ApiKey.id == key_id, ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    api_key.revoked_at = datetime.now(UTC)
    await session.commit()
