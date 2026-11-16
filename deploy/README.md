# Running on Runpod

A Runpod pod is one container, so Docker Compose is not available. This image runs every
service in that one container under [s6-overlay](https://github.com/just-containers/s6-overlay),
which starts them and restarts any that exit.

| Service | Port (localhost only, except the API) | Runs as |
|---|---|---|
| API + web app (uvicorn) | **8000, the only exposed port** | `docqa` |
| Ingestion worker (Celery) | – | `docqa` |
| PostgreSQL 18 | 5432 | `postgres` |
| Valkey 9 | 6379 | `docqa` |
| Qdrant 1.20 | 6333 / 6334 | `docqa` |
| vLLM: chat model | 8001 | root (as in the vLLM image) |
| vLLM: embedding model | 8002 | root |
| vLLM: reranker | 8003 | root |

The base is the official `vllm/vllm-openai` image (Ubuntu 24.04, CUDA 13). The app has its own
Python 3.14 environment in `/opt/docqa/venv`, separate from vLLM's Python.

## Where data lives

Everything that must survive is under `DOCQA_DATA_DIR` (default `/workspace/docqa`). Mount a
**network volume** at `/workspace` and the pod can be stopped or deleted without losing anything:

```
/workspace/docqa/postgres      database
/workspace/docqa/qdrant        vectors
/workspace/docqa/valkey        queue and cache (append-only file)
/workspace/docqa/uploads       original files
/workspace/docqa/huggingface   downloaded model weights (about 20 GB)
/workspace/docqa/secrets       generated JWT signing key
```

## Pod settings

- **Image**: `ghcr.io/<owner>/<repo>:<tag>`, built by `.github/workflows/image.yml`.
- **Expose HTTP port**: `8000`. The app is then at `https://<pod-id>-8000.proxy.runpod.net`.
- **Network volume**: mounted at `/workspace`, 50 GB or more.
- **Container disk**: 30 GB or more (the image is about 10 GB compressed).
- **GPU**: needs a host with CUDA 13 drivers. 48 GB runs the defaults comfortably; see below
  for 24 GB.
- **Environment**:
  - `DOCQA_INVITE_CODE` (required to let anyone sign up; keep it private).
  - `DOCQA_JWT_SECRET` (optional; generated and stored on the volume if unset).
  - `HF_TOKEN` (only for gated models; the defaults are open).

That URL is public. The app's own login is the only thing protecting it, and the invite code is
what stops strangers from creating accounts and using your GPU.

## Startup order and timing

Databases start at once. The API waits for them, applies migrations, then listens. The three
models load one after another (embedding, reranker, chat) so each sees how much GPU memory is
really free. The first boot downloads the weights, which takes several minutes; later boots
reuse them from the volume.

`GET /readyz` lists every dependency, including the three model servers, and returns 200 only
when all are up. Until then the web app loads but questions and uploads will fail.

## GPU memory

Each vLLM process is given a share of the GPU through these settings:

| Variable | Default | Notes |
|---|---|---|
| `DOCQA_CHAT_MODEL` | `Qwen/Qwen3-8B` | about 16.4 GB of weights at full precision |
| `DOCQA_CHAT_GPU_FRACTION` | `0.70` | weights plus the cache for context |
| `DOCQA_EMBED_GPU_FRACTION` | `0.08` | |
| `DOCQA_RERANK_GPU_FRACTION` | `0.08` | |
| `DOCQA_CHAT_MAX_MODEL_LEN` | `8192` | tokens of context for the chat model |

On a 24 GB card the defaults do not fit: use a smaller chat model, or raise
`DOCQA_CHAT_GPU_FRACTION` to about `0.78` and expect little headroom. `DOCQA_CHAT_ARGS`,
`DOCQA_EMBED_ARGS` and `DOCQA_RERANK_ARGS` append extra flags to each `vllm serve` command.

## Smoke test without a GPU

The same Dockerfile builds on a plain Ubuntu base, with the model servers switched off and the
app's fake models switched on. This checks the supervisor, databases, migrations, API, worker
and web app:

```bash
docker build -f deploy/Dockerfile --build-arg BASE_IMAGE=ubuntu:24.04 -t docqa:smoke .
docker run --rm -p 8000:8000 -v docqa-smoke:/workspace \
  -e DOCQA_MODELS=off -e DOCQA_ENVIRONMENT=dev \
  -e DOCQA_EMBEDDING_PROVIDER=fake -e DOCQA_RERANK_PROVIDER=fake -e DOCQA_CHAT_PROVIDER=fake \
  -e DOCQA_INVITE_CODE=local-invite docqa:smoke
```
