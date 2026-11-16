# DocQA

Self-hosted document question answering: upload documents, ask questions, get answers with
citations. Built on Qdrant 1.20, FastAPI and React; runs as a single GPU pod on Runpod.

Status: phases 1 to 6 of 7 done. The app has run end to end on a Runpod A40 with real models.
See [deploy/README.md](deploy/README.md) for how the single-container image works. See the build phases at the bottom.

## Local development (macOS)

Needs `uv`, Python 3.14, and a Docker runtime (Colima works: `colima start`).

```bash
docker-compose -f compose.dev.yaml up -d --wait   # Postgres 18, Valkey 9, Qdrant 1.20
cd backend
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py                     # http://127.0.0.1:8000/docs
```

Ingestion runs in a background worker; start one in a second terminal:

```bash
cd backend
uv run celery -A app.workers.celery_app worker --loglevel=info
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

## Web app

React 19, Vite, TypeScript, TanStack Query and Tailwind, in `frontend/`. Needs Node 22 and pnpm.

```bash
cd frontend
pnpm install
pnpm dev          # http://localhost:5173, forwards /api to FastAPI on port 8000
```

Run the API with `DOCQA_INVITE_CODE=<something>` set so you can create an account, and keep a
worker running so uploads get processed.

To run it the way Runpod will, as one server on one port:

```bash
cd frontend && pnpm run build
cd ../backend && DOCQA_STATIC_DIR=../frontend/dist uv run fastapi run app/main.py
```

- **Chat**: answers stream in as they are written; `[1]`-style markers open the matching source.
- **Documents**: drag-and-drop upload with progress, live ingestion status, delete.
- **API keys**: create (shown once), list, revoke.

The access token is kept in memory only; the refresh token is an httpOnly cookie, so reloading the
page signs you back in without scripts ever seeing a long-lived credential.

Checks: `pnpm run lint`, `pnpm run test`, `pnpm run build`.

## Accounts and API access

- `POST /api/auth/signup` needs the invite code from `DOCQA_INVITE_CODE`; sign-up is closed when
  that is unset.
- `POST /api/auth/login` returns a 15-minute access token and sets a 30-day refresh token as an
  httpOnly cookie. `POST /api/auth/refresh` rotates it; replaying an old one signs the user out
  everywhere.
- `GET|POST|DELETE /api/api-keys` manages personal keys. A key is shown once and stored hashed.
- Authenticate with `Authorization: Bearer <access token>` or `X-API-Key: dq_...`.
- Rate limits: 10 requests a minute per IP on auth endpoints, 120 a minute per user elsewhere.

## Documents and ingestion

- `POST /api/documents` (multipart `file`) accepts PDF, `.txt` and `.md` up to 50 MB, stores the
  file, and returns `202` with status `queued`. The same file twice from one user is a `409`.
- `GET /api/documents/{id}` reports `queued`, `processing`, `ready` or `failed` (with a reason).
- `GET /api/documents` lists your documents; `DELETE /api/documents/{id}` removes the row, the
  file and its vectors.

The worker extracts text per page (`pypdf`), splits it into sentence-aligned chunks of about 2,000
characters with 200 of overlap, embeds them, and writes them to one Qdrant collection:

- dense vectors compressed with TurboQuant (4 bits per dimension);
- a full-text index on the chunk text with BM25 scoring, used with the dense vectors for hybrid
  search (reciprocal rank fusion);
- `tenant_id` as the tenant key, and every search, count and delete filtered by it, so one user
  can never reach another's chunks.

Scanned PDFs with no text layer fail with a clear message; OCR is not supported yet.

On a Mac the default embedding model is a fake (`DOCQA_EMBEDDING_PROVIDER=fake`): hashed
bag-of-words vectors that need no GPU. Set it to `openai` with `DOCQA_EMBEDDING_URL` to use a real
model served by vLLM. Changing `DOCQA_EMBEDDING_DIMENSION` needs a new collection.

## Asking questions

`POST /api/ask` with `{"question": "...", "conversation_id": "<optional>"}` answers from your own
documents and streams the reply as server-sent events:

1. `sources`: the numbered passages the answer is written from (file, pages, snippet, score).
2. `token`: a piece of answer text, repeated as the model writes.
3. `done`: the conversation and message ids, and whether the answer came from cache. An `error`
   event takes its place if answering fails.

Behind it: the question is embedded, Qdrant returns up to 30 candidates by hybrid search within
your tenant, a reranker re-scores them, and the top 6 go to the chat model with instructions to
cite them as `[1]`, `[2]`. When nothing matches, the reply says so without calling the model.

Valkey caches two things: question embeddings for a day, and whole answers for an hour. The answer
cache is per user and keyed on a corpus version that changes on every upload or delete, so a
cached answer is never served after your documents change. Follow-up questions are not cached.

`GET /api/conversations`, `GET /api/conversations/{id}` and `DELETE /api/conversations/{id}` give
access to saved chats, each answer stored with its sources. Asking is limited to 20 a minute per
user.

Like the embedder, the reranker and chat model default to fakes on a Mac (the fake chat model
quotes the top source). The real clients speak to vLLM: `/v1/rerank` and `/v1/chat/completions`.

Tests use a separate `docqa_test` database and Valkey database 15 and a `chunks_test`
collection, and run ingestion inline, so they never touch dev data and need no worker.

## Build phases

1. Skeleton: project, health endpoints, local services, migrations, CI
2. Accounts: invite-code sign-up, login, API keys, rate limiting
3. Ingestion: upload, background pipeline, Qdrant collection
4. Ask: hybrid retrieval, rerank, streamed answers with citations, caching
5. Web app (React)
6. Runpod: single image, network volume, deploy
7. Extensions: multi-vector reranking, relevance feedback, retrieval eval
