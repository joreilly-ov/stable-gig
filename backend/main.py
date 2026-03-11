import json
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# [SECURITY: code-review] slowapi provides per-IP rate limiting; the exception
# handler converts RateLimitExceeded into a standard 429 response.
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.routers import analyse, auth, profiles, address, user_metadata


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

# [SECURITY: code-review] Shared limiter instance; routers import this to apply
# per-route limits.  key_func=get_remote_address buckets by client IP.
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Home Repair Video Analyser",
    description="Upload a home repair video; get a structured Gemini 2.5 Flash assessment.",
    version="0.2.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routers ---
app.include_router(analyse.router)
app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(address.router)
app.include_router(user_metadata.router)

# --- Frontend ---
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/", include_in_schema=False)
@app.get("/login", include_in_schema=False)
@app.get("/signup", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
def serve_frontend():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
