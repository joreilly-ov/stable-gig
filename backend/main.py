import json
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

# [SECURITY: code-review] slowapi provides per-IP rate limiting; the exception
# handler converts RateLimitExceeded into a standard 429 response.
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# Must be defined before routers are imported: auth.py does `from main import limiter`
# at module level, which would fail with ImportError if limiter isn't yet assigned.
limiter = Limiter(key_func=get_remote_address)

from app.routers import analyse, auth, profiles, address, user_metadata, photo_analysis, task_breakdown, jobs, bids, reviews, rfp, contractor_matching, escrow, contractor_connect, questions, notifications, milestones, contractor_documents


class _JsonFormatter(logging.Formatter):
    """Single-line JSON logs — parsed automatically by Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        # Forward any extra fields passed via logger.error("…", extra={…})
        _SKIP = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
        for k, v in record.__dict__.items():
            if k not in _SKIP and not k.startswith("_"):
                entry[k] = v
        return json.dumps(entry, default=str)


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # Quiet down noisy uvicorn access log (Cloud Run already logs each request)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


_configure_logging()
log = logging.getLogger(__name__)

from app.services.vertical_config import get_vertical_config

app = FastAPI(
    title=get_vertical_config()["app_title"],
    description="Upload photos or video; get a structured Gemini 2.5 Flash assessment.",
    version="0.2.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Pure ASGI middleware — intentionally does NOT extend BaseHTTPMiddleware.
# BaseHTTPMiddleware buffers the entire request body before dispatch(), which
# breaks multipart file-upload streaming and causes spurious 413 errors even
# for small files.  This implementation reads only the Content-Length *header*
# and never touches the body, so upstream handlers receive the stream intact.
#
# Registration order: added FIRST so CORSMiddleware (added last) is outermost.
# Starlette builds the stack with the last-registered middleware outermost, so
# every response from this middleware — including the early 413 — is wrapped by
# CORSMiddleware before it leaves the ASGI server.
_MAX_UPLOAD_BYTES = 350 * 1024 * 1024  # 350 MB — matches the per-route limit


class _MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            cl = Headers(scope=scope).get("content-length")
            if cl:
                try:
                    if int(cl) > _MAX_UPLOAD_BYTES:
                        limit_mb = _MAX_UPLOAD_BYTES // (1024 * 1024)
                        response = JSONResponse(
                            status_code=413,
                            content={
                                "detail": (
                                    f"File exceeds the {limit_mb} MB upload limit. "
                                    "Please trim the video and try again."
                                )
                            },
                            # Explicit CORS header: if somehow this response reaches
                            # the browser without going through CORSMiddleware (e.g.
                            # during a GFE bypass edge-case), the header is still set.
                            headers={"Access-Control-Allow-Origin": "*"},
                        )
                        await response(scope, receive, send)
                        return
                except ValueError:
                    pass
        await self.app(scope, receive, send)


app.add_middleware(_MaxBodySizeMiddleware)  # registered first → inner layer

app.add_middleware(  # registered last → outermost layer, wraps _MaxBodySizeMiddleware
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Startup health checks ---
@app.on_event("startup")
async def startup_checks():
    """Log configuration status for optional services."""
    from app.config import settings
    from app.services.push_service import _vapid_configured
    
    if not _vapid_configured():
        log.error(
            "CRITICAL: VAPID not configured. Push notifications disabled.",
            extra={
                "hint": "Set VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_CLAIMS_EMAIL in environment.",
            },
        )
    
    if not settings.stripe_secret_key:
        log.warning("Stripe secret key not configured. Payment processing disabled.")


# --- Feature flags endpoint ---
@app.get("/config/feature-flags", tags=["config"])
async def feature_flags():
    """Return the status of optional features.
    
    Frontend can use this to gracefully degrade (e.g., hide push-notification buttons
    if push is disabled) without making extra API calls.
    """
    from app.config import settings
    from app.services.push_service import _vapid_configured
    
    return {
        "push_notifications_enabled": _vapid_configured(),
        "stripe_enabled": bool(settings.stripe_secret_key),
        "smarty_address_enabled": bool(settings.smarty_auth_id),
    }


# --- Vertical config endpoint ---
@app.get("/api/vertical", tags=["config"])
async def vertical_config():
    """Return the active vertical configuration for the frontend.

    The frontend uses this to populate category dropdowns and update
    domain-specific labels (owner, provider, job type, app title).
    """
    from app.config import settings as _settings
    from app.services.vertical_config import get_vertical_config
    vcfg = get_vertical_config()
    return {
        "vertical":          _settings.vertical,
        "app_title":         vcfg["app_title"],
        "owner_label":       vcfg["owner_label"],
        "provider_label":    vcfg["provider_label"],
        "providers_label":   vcfg["providers_label"],
        "job_label":         vcfg["job_label"],
        "categories":        vcfg["categories_display"],
        "job_activities":    sorted(vcfg["job_activities"]),
        "photo_categories":  sorted(vcfg["photo_categories"]),
    }


# --- Routers ---
app.include_router(analyse.router)
app.include_router(photo_analysis.router)   # TradePhotoAnalyzer — POST /analyse/photos
app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(address.router)
app.include_router(user_metadata.router)
app.include_router(reviews.router)
app.include_router(rfp.router)
app.include_router(contractor_matching.router)
app.include_router(escrow.router)
app.include_router(contractor_connect.router)
app.include_router(questions.router)
app.include_router(notifications.router)
app.include_router(milestones.router)
app.include_router(contractor_documents.router)

# --- Frontend ---
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/", include_in_schema=False)
@app.get("/login", include_in_schema=False)
@app.get("/signup", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
def serve_frontend():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
