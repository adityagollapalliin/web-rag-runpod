import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_hasher = PasswordHasher()
# Verified against when the email is unknown, so both login failures cost the same time.
DUMMY_PASSWORD_HASH = _hasher.hash(secrets.token_urlsafe(16))

API_KEY_PREFIX = "dq_"
_JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


def create_access_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "typ": "access",
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID | None:
    """Return the user id, or None for any invalid, expired or wrong-type token."""
    try:
        claims = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[_JWT_ALGORITHM],
            options={"require": ["sub", "exp", "typ"]},
        )
        if claims["typ"] != "access":
            return None
        return uuid.UUID(claims["sub"])
    except jwt.InvalidTokenError, ValueError:
        return None


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def new_api_key() -> tuple[str, str]:
    """Return (full key shown once to the user, short display prefix)."""
    key = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return key, key[:11]


def hash_token(token: str) -> str:
    """SHA-256 is enough for high-entropy random tokens (unlike passwords)."""
    return hashlib.sha256(token.encode()).hexdigest()
