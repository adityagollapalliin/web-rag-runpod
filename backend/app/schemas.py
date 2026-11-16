import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    invite_code: str = Field(max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    created_at: datetime


class ApiKeyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreatedOut(ApiKeyOut):
    key: str  # shown once, never retrievable again


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    kind: str
    size_bytes: int
    status: str  # queued | processing | ready | failed
    error: str | None
    page_count: int | None
    chunk_count: int | None
    created_at: datetime
    updated_at: datetime


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # Omit to start a new conversation; pass an id to ask a follow-up.
    conversation_id: uuid.UUID | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    citations: list[dict[str, Any]]
    rating: int | None
    created_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class RatingIn(BaseModel):
    # 1 = helpful, -1 = not helpful, 0 = clear the rating
    rating: Literal[-1, 0, 1]
