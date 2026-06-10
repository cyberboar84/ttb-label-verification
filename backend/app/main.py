"""FastAPI application: single + batch label verification, plus the static UI.

Endpoints
  GET  /health             liveness + whether we're in mock mode
  POST /api/verify         one image (+ optional application JSON) -> result
  POST /api/verify/batch   many images (+ optional manifest)       -> results
  GET  /                   the agent-facing UI (static)

Security posture (see SECURITY.md): uploads are size- and content-validated,
the paid model endpoints are rate-limited per IP, and the app refuses to start
in mock mode when REQUIRE_AZURE is set.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .models import ApplicationData, VerificationResult
from .pipeline import verify_bottle, verify_label
from .preprocess import InvalidImage, validate_image
from .ratelimit import DailyCircuitBreaker, RateLimiter, client_ip


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast: never silently serve fabricated verdicts in a real environment.
    if settings.require_azure and settings.use_mock:
        raise RuntimeError(
            "REQUIRE_AZURE is set but Vision/OpenAI credentials are missing or "
            "incomplete, refusing to start in mock mode."
        )
    # Warm the model clients at boot so the first real request isn't a cold
    # start. Always On keeps the worker warm thereafter. Best-effort + bounded so
    # a slow/unreachable service never blocks startup.
    if not settings.use_mock:
        from .extract import warmup as warm_aoai
        from .vision import warmup as warm_vision
        try:
            await asyncio.wait_for(
                asyncio.gather(warm_vision(), warm_aoai()), timeout=20)
        except Exception:
            pass
    yield


app = FastAPI(title="TTB Label Verification", version="1.0.0", lifespan=lifespan)

_limiter = RateLimiter(settings.rate_limit_per_min)
_breaker = DailyCircuitBreaker(settings.daily_call_cap)
_CAP_MSG = ("Daily capacity limit for this prototype has been reached. "
            "Please try again tomorrow.")


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    """Per-IP token bucket on the paid model endpoints (cost-abuse / DoS guard)."""
    if request.url.path.startswith("/api/"):
        if not _limiter.allow(client_ip(request)):
            return JSONResponse(
                {"detail": "Rate limit exceeded. Please slow down and retry."},
                status_code=429,
            )
    return await call_next(request)


@app.middleware("http")
async def _revalidate_static(request: Request, call_next):
    """Force the browser to revalidate the static UI (HTML/JS/CSS) so a deploy is
    never masked by a stale cached asset. StaticFiles still answers with 304 when
    nothing changed, so this costs a conditional request, not a re-download."""
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") and (
        path == "/" or path.endswith((".html", ".js", ".css"))
    ):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": "mock" if settings.use_mock else "azure",
        "vision_ready": settings.vision_ready,
        "aoai_ready": settings.aoai_ready,
    }


async def _read_limited(upload: UploadFile) -> bytes:
    """Stream the upload, aborting if it exceeds the configured cap so an
    oversized file is never fully buffered in memory. Raises ValueError if over."""
    max_bytes = settings.max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Image exceeds the {settings.max_upload_mb} MB limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_application(raw: str | None) -> ApplicationData:
    if not raw or not raw.strip():
        return ApplicationData()  # empty -> compliance-only flow
    try:
        return ApplicationData(**json.loads(raw))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid application JSON: {e}")


@app.post("/api/verify", response_model=VerificationResult)
async def verify_single(
    images: list[UploadFile] | None = File(None),
    image: UploadFile | None = File(None),
    application: str | None = Form(None),
    label_id: str | None = Form(None),
):
    """Verify one bottle from one or more panel images (front / back / side).

    Accepts `images` (multiple panels of the same bottle) and/or the legacy
    single `image` field. With no application data, runs the zero-input
    compliance check; with application data, also matches each field against it.
    """
    panels = list(images or [])
    if image is not None:
        panels.append(image)
    if not panels:
        raise HTTPException(status_code=400, detail="No image provided.")

    app_data = _parse_application(application)
    blobs: list[bytes] = []
    for panel in panels:
        if not panel.content_type or not panel.content_type.startswith("image/"):
            raise HTTPException(status_code=415, detail="All files must be images.")
        try:
            data = await _read_limited(panel)
        except ValueError as e:
            raise HTTPException(status_code=413, detail=str(e))
        if not data:
            continue
        try:
            validate_image(data)
        except InvalidImage as e:
            raise HTTPException(status_code=415, detail=str(e))
        blobs.append(data)
    if not blobs:
        raise HTTPException(status_code=400, detail="Empty image upload.")
    if not _breaker.allow(len(blobs)):
        raise HTTPException(status_code=503, detail=_CAP_MSG)

    try:
        return await asyncio.wait_for(
            verify_bottle(blobs, app_data, label_id=label_id or panels[0].filename),
            timeout=settings.request_timeout_s,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504,
                            detail="Verification timed out, please try again.")


@app.post("/api/verify/batch")
async def verify_batch(
    images: list[UploadFile] = File(...),
    manifest: str = Form("{}"),
):
    """Batch endpoint for importer dumps (Janet's 200-300 at once).

    manifest is an optional JSON object mapping filename -> application fields:
      {"old_tom.jpg": {"brand_name": "Old Tom Distillery", "alcohol_content": "45%"}}
    Files with no manifest entry are checked for compliance only. Files are
    processed with bounded concurrency; one bad file never sinks the batch.
    """
    if len(images) > settings.max_batch_files:
        raise HTTPException(status_code=413,
                            detail=f"Batch exceeds the {settings.max_batch_files}-file limit.")
    try:
        manifest_map = json.loads(manifest)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid manifest JSON: {e}")
    if not isinstance(manifest_map, dict):
        raise HTTPException(status_code=422,
                            detail="Manifest must be a JSON object mapping filename -> fields.")
    if not _breaker.allow(len(images)):
        raise HTTPException(status_code=503, detail=_CAP_MSG)

    sem = asyncio.Semaphore(settings.batch_concurrency)

    async def run_one(upload: UploadFile) -> dict:
        async with sem:
            try:
                entry = manifest_map.get(upload.filename, {})
                if not isinstance(entry, dict):
                    raise ValueError("manifest entry must be a JSON object")
                app_data = ApplicationData(**entry)
                data = await _read_limited(upload)
                validate_image(data)
                result = await asyncio.wait_for(
                    verify_label(data, app_data, label_id=upload.filename),
                    timeout=settings.request_timeout_s)
                return json.loads(result.model_dump_json())
            except Exception as e:  # isolate per-file failures
                return {"label_id": upload.filename, "overall": "MISMATCH",
                        "error": str(e), "fields": [], "warning": None}

    results = await asyncio.gather(*(run_one(u) for u in images))
    summary = {"total": len(results),
               "flagged": sum(1 for r in results if r.get("overall") != "PASS")}
    return JSONResponse({"summary": summary, "results": results})


# Static UI mounted last so /api routes take precedence. We probe a couple of
# candidate locations so the same code works locally (repo/frontend, app under
# repo/backend/app) and when deployed (wwwroot/frontend, app under wwwroot/app).
_HERE = os.path.dirname(__file__)
_FRONTEND_CANDIDATES = [
    os.path.join(_HERE, "..", "frontend"),        # deployed: sibling of app/
    os.path.join(_HERE, "..", "..", "frontend"),  # local dev: repo/frontend
]
for _cand in _FRONTEND_CANDIDATES:
    if os.path.isdir(_cand):
        app.mount("/", StaticFiles(directory=_cand, html=True), name="frontend")
        break
