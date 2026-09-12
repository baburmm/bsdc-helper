"""BSDC Helper — a small single-login file drop for moving files between machines."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, storage

log = logging.getLogger("bsdc-helper")
logging.basicConfig(level=logging.INFO)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not config.ADMIN_PASSWORD:
        log.warning("ADMIN_PASSWORD is not set — nobody will be able to sign in.")
    if config.SECRET_KEY_WAS_GENERATED:
        log.warning("SECRET_KEY not set — a random one was generated; sessions reset on restart.")
    log.info("storage backend: %s", storage.get_backend().kind)
    yield


app = FastAPI(
    title="BSDC Helper",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # Static assets carry an ETag, so "no-cache" means revalidate-then-304 rather
    # than refetch. Without it a browser can keep serving app.js from a previous
    # revision after a redeploy.
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# --- pages ------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


# --- auth api ---------------------------------------------------------------

@app.post("/api/login")
async def api_login(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    auth.check_rate_limit(client_ip)

    body = await request.json()
    password = str(body.get("password", ""))

    if not auth.verify_password(password):
        auth.record_failure(client_ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect password")

    response = JSONResponse({"ok": True})
    auth.issue_session(response)
    return response


@app.post("/api/logout")
def api_logout():
    response = JSONResponse({"ok": True})
    auth.clear_session(response)
    return response


# --- file api ---------------------------------------------------------------

@app.get("/api/files")
def api_list(_: str = Depends(auth.require_user)):
    files = storage.get_backend().list()
    return {
        "files": [
            {"name": f.name, "size": f.size, "modified": f.modified.isoformat()}
            for f in files
        ],
        "limits": {
            "max_mb": config.MAX_UPLOAD_MB,
            "allowed": sorted(config.ALLOWED_EXTENSIONS),
        },
    }


@app.post("/api/upload")
async def api_upload(
    file: UploadFile = File(...),
    _: str = Depends(auth.require_user),
):
    name = storage.safe_name(file.filename or "")
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'.{ext or name}' is not an allowed file type. "
            f"Allowed: {', '.join('.' + e for e in sorted(config.ALLOWED_EXTENSIONS))}",
        )

    size = file.size or 0
    if size > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is larger than the {config.MAX_UPLOAD_MB} MB limit.",
        )

    await file.seek(0)
    stored = storage.get_backend().save(name, file.file)
    log.info("uploaded %s (%d bytes)", stored, size)
    return {"ok": True, "name": stored, "size": size}


@app.get("/api/download/{name}")
def api_download(name: str, _: str = Depends(auth.require_user)):
    backend = storage.get_backend()
    if not backend.exists(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    chunks, size, ctype = backend.read(name)
    safe = storage.safe_name(name)
    disposition = f"attachment; filename*=UTF-8''{quote(safe)}"
    return StreamingResponse(
        chunks,
        media_type=ctype,
        headers={"Content-Disposition": disposition, "Content-Length": str(size)},
    )


@app.delete("/api/files/{name}")
def api_delete(name: str, _: str = Depends(auth.require_user)):
    if not storage.get_backend().delete(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    log.info("deleted %s", name)
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """JSON for the API, a redirect to /login for page requests that lost the session."""
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and not request.url.path.startswith("/api/"):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
