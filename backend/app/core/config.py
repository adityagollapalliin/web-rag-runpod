from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-change-me-dev-only-secret-change-me"  # noqa: S105


class Settings(BaseSettings):
    """All configuration comes from DOCQA_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="DOCQA_", env_file=".env", extra="ignore")

    environment: str = "dev"  # dev | test | prod
    database_url: str = "postgresql+asyncpg://docqa:docqa@localhost:55432/docqa"
    valkey_url: str = "valkey://localhost:56379/0"
    qdrant_url: str = "http://localhost:56333"
    readiness_timeout_seconds: float = 2.0

    # Auth
    jwt_secret: str = DEV_JWT_SECRET
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    # Sign-up is closed unless this is set; share it only with people you want on your GPU.
    invite_code: str = ""

    # Rate limits (fixed one-minute windows)
    auth_rate_limit_per_minute: int = 10  # per client IP, on sign-up/login/refresh
    api_rate_limit_per_minute: int = 120  # per signed-in user
    # Header holding the real client IP when behind a proxy you trust (Runpod: CF-Connecting-IP).
    client_ip_header: str = ""

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @model_validator(mode="after")
    def _require_real_secret_in_prod(self) -> Self:
        if self.is_prod and self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError("DOCQA_JWT_SECRET must be set in prod")
        if len(self.jwt_secret) < 32:
            raise ValueError("DOCQA_JWT_SECRET must be at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
