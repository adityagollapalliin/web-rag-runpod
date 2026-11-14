# DocQA

Self-hosted document question answering: upload documents, ask questions, get answers with
citations. Built on Qdrant 1.20, FastAPI and React; runs as a single GPU pod on Runpod.

Status: phases 1 and 2 of 7 done (backend skeleton, accounts). See the build phases at the bottom.

## Local development (macOS)

Needs `uv`, Python 3.14, and a Docker runtime (Colima works: `colima start`).

```bash
docker-compose -f compose.dev.yaml up -d --wait   # Postgres 18, Valkey 9, Qdrant 1.20
cd backend
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py                     # http://127.0.0.1:8000/docs
```

Checks:

```bash
cd backend
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy app tests
```

- `GET /healthz`: the API process is up.
- `GET /readyz`: Postgres, Valkey and Qdrant all answer (503 otherwise).

Configuration is by `DOCQA_*` environment variables; see `backend/.env.example`.

## Accounts and API access

- `POST /api/auth/signup` needs the invite code from `DOCQA_INVITE_CODE`; sign-up is closed when
  that is unset.
- `POST /api/auth/login` returns a 15-minute access token and sets a 30-day refresh token as an
  httpOnly cookie. `POST /api/auth/refresh` rotates it; replaying an old one signs the user out
  everywhere.
- `GET|POST|DELETE /api/api-keys` manages personal keys. A key is shown once and stored hashed.
- Authenticate with `Authorization: Bearer <access token>` or `X-API-Key: dq_...`.
- Rate limits: 10 requests a minute per IP on auth endpoints, 120 a minute per user elsewhere.

Tests use a separate `docqa_test` database and Valkey database 15, so they never touch dev data.

## Build phases

1. Skeleton: project, health endpoints, local services, migrations, CI
2. Accounts: invite-code sign-up, login, API keys, rate limiting
3. Ingestion: upload, background pipeline, Qdrant collection
4. Ask: hybrid retrieval, rerank, streamed answers with citations, caching
5. Web app (React)
6. Runpod: single image, network volume, deploy
7. Extensions: multi-vector reranking, relevance feedback, retrieval eval
