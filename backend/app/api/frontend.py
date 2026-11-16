"""Serve the built React app from the same origin as the API (single port on Runpod)."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_API_PREFIXES = ("api/", "healthz", "readyz", "docs", "redoc", "openapi.json")


def mount_frontend(app: FastAPI, static_dir: Path) -> None:
    """Call after all API routers: the catch-all route must be registered last."""
    root = static_dir.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"DOCQA_STATIC_DIR has no index.html: {root}")
    if (root / "assets").is_dir():
        # Vite fingerprints these filenames, so they can be cached for a long time.
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(_API_PREFIXES):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        # Client-side routes such as /chat/123 all load the app, which then routes itself.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
