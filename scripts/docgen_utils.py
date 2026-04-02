"""Utilities for generating docs from FastAPI code in constrained environments."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


def _ensure_module(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is not None:
        return module
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def install_import_stubs() -> None:
    """Install lightweight stubs for optional deps used at import time."""

    genai = _ensure_module("google.generativeai")
    if not hasattr(genai, "configure"):
        genai.configure = lambda **_: None
    if not hasattr(genai, "GenerativeModel"):
        class _DummyModel:
            def __init__(self, *args, **kwargs):
                pass

            def generate_content(self, *args, **kwargs):
                return MagicMock()

        genai.GenerativeModel = _DummyModel

    _ensure_module("google")
    _ensure_module("google.generativeai.types")

    supabase = _ensure_module("supabase")
    if not hasattr(supabase, "create_client"):
        supabase.create_client = lambda *args, **kwargs: MagicMock()
    if not hasattr(supabase, "Client"):
        class _DummyClient:  # pragma: no cover - docgen shim
            pass

        supabase.Client = _DummyClient

    pywebpush = _ensure_module("pywebpush")
    if not hasattr(pywebpush, "webpush"):
        pywebpush.webpush = lambda *args, **kwargs: None
    if not hasattr(pywebpush, "WebPushException"):
        pywebpush.WebPushException = Exception

    stripe = _ensure_module("stripe")
    if not hasattr(stripe, "api_key"):
        stripe.api_key = ""

    httpx = _ensure_module("httpx")
    if not hasattr(httpx, "HTTPError"):
        httpx.HTTPError = Exception
    if not hasattr(httpx, "TimeoutException"):
        httpx.TimeoutException = Exception
    if not hasattr(httpx, "Response"):
        class _DummyResponse:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {}

        httpx.Response = _DummyResponse
    if not hasattr(httpx, "AsyncClient"):
        class _DummyAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, *args, **kwargs):
                return httpx.Response()

        httpx.AsyncClient = _DummyAsyncClient

    tenacity = _ensure_module("tenacity")
    if not hasattr(tenacity, "retry"):
        def _retry(*args, **kwargs):
            def _decorator(func):
                return func

            return _decorator

        tenacity.retry = _retry
    if not hasattr(tenacity, "stop_after_attempt"):
        tenacity.stop_after_attempt = lambda *args, **kwargs: None
    if not hasattr(tenacity, "wait_exponential"):
        tenacity.wait_exponential = lambda *args, **kwargs: None
    if not hasattr(tenacity, "retry_if_exception_type"):
        tenacity.retry_if_exception_type = lambda *args, **kwargs: None

    _ensure_module("hachoir")
    parser = _ensure_module("hachoir.parser")
    if not hasattr(parser, "createParser"):
        parser.createParser = lambda *args, **kwargs: None
    metadata = _ensure_module("hachoir.metadata")
    if not hasattr(metadata, "extractMetadata"):
        metadata.extractMetadata = lambda *args, **kwargs: None

    mutagen = _ensure_module("mutagen")
    if not hasattr(mutagen, "File"):
        mutagen.File = lambda *args, **kwargs: None

    pil = _ensure_module("PIL")
    pil_image = _ensure_module("PIL.Image")
    pil_filter = _ensure_module("PIL.ImageFilter")

    if not hasattr(pil_image, "Image"):
        class _DummyImage:
            pass

        pil_image.Image = _DummyImage

    if not hasattr(pil_image, "open"):
        pil_image.open = lambda *args, **kwargs: MagicMock()

    if not hasattr(pil_filter, "FIND_EDGES"):
        pil_filter.FIND_EDGES = object()

    if not hasattr(pil, "Image"):
        pil.Image = pil_image
    if not hasattr(pil, "ImageFilter"):
        pil.ImageFilter = pil_filter


def load_app():
    """Import and return FastAPI app from backend/main.py."""
    repo_root = Path(__file__).resolve().parents[1]
    backend_dir = repo_root / "backend"

    install_import_stubs()
    sys.path.insert(0, str(backend_dir))

    os.environ.setdefault("GEMINI_API_KEY", "docgen-key")
    os.environ.setdefault("SUPABASE_URL", "https://docgen.supabase.co")
    os.environ.setdefault("SUPABASE_ANON_KEY", "docgen-anon")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "docgen-service")

    original_cwd = Path.cwd()
    os.chdir(backend_dir)
    try:
        from main import app  # pylint: disable=import-error
    finally:
        os.chdir(original_cwd)

    return app
