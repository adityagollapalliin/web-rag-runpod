# One image holding every service, because a Runpod pod is a single container
# (no Docker Compose). s6-overlay supervises the processes.
#
# Build from the repository root:  docker build -f deploy/Dockerfile .
# For a GPU-less smoke test, pass --build-arg BASE_IMAGE=ubuntu:24.04 and run with DOCQA_MODELS=off.

ARG BASE_IMAGE=vllm/vllm-openai:v0.32.0

# ---- Web app ----------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

# ---- Valkey, compiled on the same OS as the final image (Ubuntu 24.04) --------
FROM ubuntu:24.04 AS valkey
ARG VALKEY_VERSION=9.1.3
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://github.com/valkey-io/valkey/archive/refs/tags/${VALKEY_VERSION}.tar.gz" | tar xz \
    && make -C "valkey-${VALKEY_VERSION}" -j"$(nproc)" BUILD_TLS=no \
    && install -D -t /out "valkey-${VALKEY_VERSION}/src/valkey-server" "valkey-${VALKEY_VERSION}/src/valkey-cli"

# ---- Final image ------------------------------------------------------------
FROM ${BASE_IMAGE}
ARG TARGETARCH
ARG S6_OVERLAY_VERSION=3.2.3.2
ARG QDRANT_VERSION=v1.20.0
ARG POSTGRES_MAJOR=18
ENV DEBIAN_FRONTEND=noninteractive

# PostgreSQL from the official PGDG repository; no cluster is created at install time
# because the data directory lives on the network volume and is initialised at first boot.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl xz-utils postgresql-common \
    && sed -i 's/^#\?create_main_cluster.*/create_main_cluster = false/' /etc/postgresql-common/createcluster.conf \
    && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
    && apt-get install -y --no-install-recommends "postgresql-${POSTGRES_MAJOR}" \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/usr/lib/postgresql/${POSTGRES_MAJOR}/bin:${PATH}"

# Qdrant (static musl build) and s6-overlay, for the image's CPU architecture.
RUN case "${TARGETARCH}" in amd64) arch=x86_64 ;; arm64) arch=aarch64 ;; *) echo "unsupported: ${TARGETARCH}"; exit 1 ;; esac \
    && curl -fsSL "https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/qdrant-${arch}-unknown-linux-musl.tar.gz" \
       | tar xz -C /usr/local/bin qdrant \
    && curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz" | tar -Jxp -C / \
    && curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${arch}.tar.xz" | tar -Jxp -C /

COPY --from=valkey /out/ /usr/local/bin/
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

# The app gets its own Python 3.14 environment, separate from vLLM's.
# The vLLM base image sets UV_OVERRIDE and UV_INDEX_STRATEGY for its own build; they must
# not leak into ours, or the lockfile would not be honoured.
RUN useradd --system --create-home --home-dir /home/docqa --shell /usr/sbin/nologin docqa
WORKDIR /opt/docqa/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN env -u UV_OVERRIDE -u UV_INDEX_STRATEGY \
      UV_PYTHON_INSTALL_DIR=/opt/docqa/python UV_PROJECT_ENVIRONMENT=/opt/docqa/venv UV_COMPILE_BYTECODE=1 \
      uv sync --locked --no-dev --no-install-project --python 3.14 \
    && rm -rf /root/.cache/uv /opt/uv/cache
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY --from=web /web/dist /opt/docqa/web

COPY deploy/s6-rc.d/ /etc/s6-overlay/s6-rc.d/
COPY deploy/lib.sh /opt/docqa/lib.sh
RUN chmod +x /etc/s6-overlay/s6-rc.d/*/run

# Defaults for the pod. Secrets (DOCQA_INVITE_CODE, optionally DOCQA_JWT_SECRET) come from
# the pod's environment; everything here can be overridden the same way.
ENV S6_KEEP_ENV=1 \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0 \
    PATH="/opt/docqa/venv/bin:${PATH}" \
    DOCQA_DATA_DIR=/workspace/docqa \
    DOCQA_MODELS=on \
    DOCQA_ENVIRONMENT=prod \
    DOCQA_DATABASE_URL=postgresql+asyncpg://docqa@127.0.0.1:5432/docqa \
    DOCQA_VALKEY_URL=valkey://127.0.0.1:6379/0 \
    DOCQA_CELERY_BROKER_URL=redis://127.0.0.1:6379/1 \
    DOCQA_QDRANT_URL=http://127.0.0.1:6333 \
    DOCQA_STATIC_DIR=/opt/docqa/web \
    DOCQA_CLIENT_IP_HEADER=CF-Connecting-IP \
    DOCQA_EMBEDDING_PROVIDER=openai \
    DOCQA_EMBEDDING_URL=http://127.0.0.1:8002 \
    DOCQA_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B \
    DOCQA_EMBEDDING_DIMENSION=1024 \
    DOCQA_RERANK_PROVIDER=http \
    DOCQA_RERANK_URL=http://127.0.0.1:8003 \
    DOCQA_RERANK_MODEL=Qwen/Qwen3-Reranker-0.6B \
    DOCQA_CHAT_PROVIDER=openai \
    DOCQA_CHAT_URL=http://127.0.0.1:8001 \
    DOCQA_CHAT_MODEL=Qwen/Qwen3-8B \
    DOCQA_CHAT_GPU_FRACTION=0.70 \
    DOCQA_EMBED_GPU_FRACTION=0.08 \
    DOCQA_RERANK_GPU_FRACTION=0.08 \
    DOCQA_CHAT_MAX_MODEL_LEN=8192
# Qwen3 specifics: the embedder wants an instruction before a query, and the chat model
# answers faster and more plainly with its thinking mode off.
ENV DOCQA_EMBEDDING_QUERY_PREFIX="Instruct: Given a question, retrieve passages that answer it\nQuery: " \
    DOCQA_CHAT_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}'

EXPOSE 8000
ENTRYPOINT ["/init"]
