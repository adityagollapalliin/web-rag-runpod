from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

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

    # Documents and ingestion
    storage_dir: Path = Path("var/uploads")  # Runpod: /workspace/uploads
    max_upload_mb: int = 50
    chunk_max_chars: int = 2000  # roughly 500 tokens
    chunk_overlap_chars: int = 200
    qdrant_collection: str = "chunks"
    # Background jobs. Valkey speaks the Redis protocol, hence the redis:// scheme.
    celery_broker_url: str = "redis://localhost:56379/1"
    celery_eager: bool = False  # run jobs inline instead of queueing (tests)

    # Embedding model. "fake" needs no GPU; "openai" calls an OpenAI-style server (vLLM).
    embedding_provider: Literal["fake", "openai"] = "fake"
    embedding_url: str = "http://127.0.0.1:8002"
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimension: int = 1024

    # Text sent before a question when embedding it (some models want an instruction here).
    embedding_query_prefix: str = ""

    # Reranker and chat model: same "fake" | real split as the embedder.
    rerank_provider: Literal["fake", "http"] = "fake"
    rerank_url: str = "http://127.0.0.1:8003"
    rerank_model: str = "Qwen/Qwen3-Reranker-0.6B"
    chat_provider: Literal["fake", "openai"] = "fake"
    chat_url: str = "http://127.0.0.1:8001"
    chat_model: str = ""
    chat_max_tokens: int = 800

    # Answering
    retrieve_limit: int = 30  # candidates fetched from Qdrant before reranking
    context_chunks: int = 6  # passages given to the chat model
    answer_cache_seconds: int = 3600
    ask_rate_limit_per_minute: int = 20  # per user; each ask costs GPU time

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @model_validator(mode="after")
    def _require_real_secret_in_prod(self) -> Self:
        if self.is_prod and self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError("DOCQA_JWT_SECRET must be set in prod")
        providers = (self.embedding_provider, self.rerank_provider, self.chat_provider)
        if self.is_prod and "fake" in providers:
            raise ValueError("Fake model providers are not allowed in prod")
        if self.chat_provider == "openai" and not self.chat_model:
            raise ValueError("DOCQA_CHAT_MODEL must be set when DOCQA_CHAT_PROVIDER=openai")
        if len(self.jwt_secret) < 32:
            raise ValueError("DOCQA_JWT_SECRET must be at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
