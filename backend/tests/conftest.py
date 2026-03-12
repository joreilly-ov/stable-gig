"""
Shared test configuration and fixtures.

Boot order — this file is executed by pytest before any test module:

  1. sys.modules stubs   — pre-empt heavy C-extension packages that panic in
                           this sandbox environment (cryptography/_cffi_backend).
                           In a real deployment these packages work fine; the stubs
                           are only needed so unit tests can import the app without
                           triggering the full dependency chain.
  2. os.environ defaults — satisfy pydantic-settings before Settings() is instantiated.
  3. Fixtures            — shared image-building helpers and pytest fixtures.
"""

import base64
import io
import os
import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# 1. Pre-stub modules that require unavailable C extensions in this env.
#    Must happen before ANY app module is imported.
#
#    Why: google.generativeai and supabase both transitively import
#    cryptography.hazmat.bindings._rust (a pyo3 Rust extension) which
#    panics here because _cffi_backend is missing.  By inserting MagicMocks
#    into sys.modules first, Python's import system returns them immediately
#    without executing the real __init__.py files.
# ---------------------------------------------------------------------------

def _stub(name: str) -> MagicMock:
    mock = MagicMock()
    sys.modules[name] = mock
    return mock

# google-generativeai and its dependency chain
_stub("google.generativeai")
_stub("google.generativeai.types")

# supabase / gotrue dependency chain
_supabase_mock = _stub("supabase")
_supabase_mock.create_client.return_value = MagicMock()

# ---------------------------------------------------------------------------
# 2. Stub all external service credentials so the app can be imported
# ---------------------------------------------------------------------------
os.environ.setdefault("GEMINI_API_KEY",       "test-gemini-key-000")
os.environ.setdefault("SUPABASE_URL",         "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY",    "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pytest
from PIL import Image, ImageDraw


# ---------------------------------------------------------------------------
# Image-building helpers (not fixtures — call them directly in tests)
# ---------------------------------------------------------------------------

def make_solid_png(width: int = 200, height: int = 200,
                   color: tuple = (100, 120, 140)) -> bytes:
    """
    Return PNG bytes of a plain solid-color image.
    Solid colors produce near-zero FIND_EDGES output → sharpness_score ≈ 0
    → the blurry flag is triggered.
    """
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_checkerboard_jpeg(size: int = 200, square: int = 10) -> bytes:
    """
    Return JPEG bytes of a black-and-white checkerboard.
    High-frequency edges → sharpness_score well above BLUR_THRESHOLD (6.0).
    """
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    for row in range(0, size, square):
        for col in range(0, size, square):
            if (row // square + col // square) % 2:
                draw.rectangle([col, row, col + square - 1, row + square - 1],
                               fill="black")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def as_data_uri(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """Encode raw image bytes as a base64 data URI."""
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sharp_data_uri() -> str:
    """A valid, sharp JPEG image as a data URI — passes all preprocessing."""
    return as_data_uri(make_checkerboard_jpeg(), mime="image/jpeg")


@pytest.fixture()
def blurry_data_uri() -> str:
    """A valid but blurry (solid-color) PNG image as a data URI."""
    return as_data_uri(make_solid_png(), mime="image/png")


@pytest.fixture()
def tiny_data_uri() -> str:
    """An image smaller than _MIN_DIMENSION (80 px) — should be rejected."""
    return as_data_uri(make_solid_png(width=40, height=40), mime="image/png")


@pytest.fixture()
def large_data_uri() -> str:
    """An image larger than _MAX_DIMENSION (1200 px) — should be resized."""
    return as_data_uri(make_checkerboard_jpeg(size=2000), mime="image/jpeg")


@pytest.fixture()
def corrupt_data_uri() -> str:
    """Random bytes that are not a valid image."""
    return as_data_uri(b"\x00\xFF\xAB\xCD" * 50, mime="image/jpeg")
