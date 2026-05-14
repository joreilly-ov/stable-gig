"""Integration tests for POST /analyse  (video and image upload endpoint).

A minimal FastAPI app is used to avoid the main.py circular-import issue.

All external calls are mocked:
  - app.services.gemini.analyse        (Gemini SDK — runs in to_thread)
  - app.services.photo_analyzer.analyse (photo branch)
  - app.services.video_meta.extract_video_metadata
  - app.services.usage_logger.log_usage
  - app.database.get_supabase_admin    (for _store_result — service role bypasses RLS)
  - app.dependencies.get_optional_user (auth dependency)

Coverage
--------
Magic-byte validation (_assert_video_magic unit tests):
  - MP4 / MOV (ftyp at offset 4)
  - WebM / Matroska (0x1a45dfa3)
  - AVI (RIFF…AVI)
  - MPEG Program Stream (0x000001ba)
  - MPEG Elementary Stream (0x000001b3)
  - MPEG-TS (sync byte 0x47)
  - Header too small → 400
  - Unrecognised bytes → 400

Magic-byte validation (_assert_image_magic unit tests):
  - JPEG (FF D8 FF)
  - PNG (8-byte sig)
  - WebP (RIFF....WEBP)
  - Header too small → 400
  - Unrecognised bytes → 400

Endpoint — video:
  - Unsupported Content-Type → 400
  - Invalid magic bytes → 400
  - File exceeds size limit → 413
  - Happy path unauthenticated → 200, video_metadata in body
  - Happy path authenticated → 200, _store_result called
  - Browser GPS used when video has no embedded GPS
  - Browser GPS NOT used when video already has GPS
  - _token_usage key absent from response body
  - log_usage called with correct args
  - ValueError from gemini.analyse → 422
  - quota/rate-limit error string → 429
  - Generic exception → 500, no detail leak

Endpoint — image:
  - JPEG upload → 200, photo analysis result
  - PNG upload → 200
  - WebP upload → 200
  - description form field forwarded when ≥10 chars
  - short/missing description uses fallback
  - Invalid image magic bytes → 400
  - Image exceeds 20 MB limit → 413
  - ValueError from photo_analyzer.analyse → 422
  - quota/rate-limit error → 429
  - Generic exception → 500, no detail leak
  - log_usage called with analysis_type="photo"
"""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.dependencies import get_optional_user
from app.routers.analyse import _assert_image_magic, _assert_video_magic, router

# ---------------------------------------------------------------------------
# Minimal test app
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(router)

# ---------------------------------------------------------------------------
# Magic byte constants for test files
# ---------------------------------------------------------------------------

# Each is ≥ 12 bytes so the magic check has enough data
_MP4_HEADER   = b"\x00\x00\x00\x18" b"ftyp" b"mp42" b"\x00\x00"    # ftyp at [4:8]
_WEBM_HEADER  = b"\x1a\x45\xdf\xa3" + b"\x00" * 8
_AVI_HEADER   = b"RIFF" b"\x00\x00\x00\x00" b"AVI" b"\x00"         # 12 bytes
_MPEG_PS      = b"\x00\x00\x01\xba" + b"\x00" * 8
_MPEG_ES      = b"\x00\x00\x01\xb3" + b"\x00" * 8
_MPEG_TS      = b"\x47" + b"\x00" * 11
_JPEG_HEADER  = b"\xff\xd8\xff\xe0" + b"\x00" * 8
_PNG_HEADER   = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4
_WEBP_HEADER  = b"RIFF" b"\x00\x00\x00\x00" b"WEBP"                 # 12 bytes
_SMALL_HEADER = b"\x00" * 4                           # fewer than 8 bytes

# Minimal "valid" MP4 content used as the full file body in endpoint tests
_MP4_BYTES  = _MP4_HEADER + b"\x00" * 100
_JPEG_BYTES = _JPEG_HEADER + b"\x00" * 100
_PNG_BYTES  = _PNG_HEADER  + b"\x00" * 100

# Default mock result from gemini.analyse()
_GEMINI_RESULT = {
    "problem_type": "plumbing",
    "description": "Dripping tap",
    "location_in_home": "kitchen",
    "urgency": "medium",
    "materials_involved": ["washer"],
    "clarifying_questions": ["How old is the tap?"],
    "_token_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
}

# Default mock result from photo_analyzer.analyse()
_PHOTO_RESULT = {
    "likely_issue": "Dripping tap under the sink",
    "urgency_score": 4,
    "required_tools": ["basin wrench"],
    "estimated_parts": ["tap washer"],
    "image_feedback": [{"index": 0, "role": "Wide Shot", "quality": "ok", "note": None}],
    "token_usage_estimate": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
}

_META = {}   # empty metadata from extract_video_metadata by default


# ---------------------------------------------------------------------------
# Unit tests for _assert_video_magic
# ---------------------------------------------------------------------------

class TestAssertVideoMagic:
    def test_mp4_accepted(self):
        _assert_video_magic(_MP4_HEADER)   # should not raise

    def test_webm_accepted(self):
        _assert_video_magic(_WEBM_HEADER)

    def test_avi_accepted(self):
        _assert_video_magic(_AVI_HEADER)

    def test_mpeg_ps_accepted(self):
        _assert_video_magic(_MPEG_PS)

    def test_mpeg_es_accepted(self):
        _assert_video_magic(_MPEG_ES)

    def test_mpeg_ts_accepted(self):
        _assert_video_magic(_MPEG_TS)

    def test_too_small_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _assert_video_magic(_SMALL_HEADER)
        assert exc_info.value.status_code == 400

    def test_unrecognised_bytes_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _assert_video_magic(_JPEG_HEADER)
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Endpoint fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    app.dependency_overrides[get_optional_user] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def authed_client():
    mock_user = MagicMock()
    mock_user.id = "user-uuid-123"
    app.dependency_overrides[get_optional_user] = lambda: mock_user
    yield TestClient(app)
    app.dependency_overrides.clear()


def _post(test_client, file_bytes=_MP4_BYTES, content_type="video/mp4",
          filename="clip.mp4", extra_data=None):
    data = extra_data or {}
    return test_client.post(
        "/analyse",
        files={"file": (filename, file_bytes, content_type)},
        data=data,
    )


def _patches(gemini_result=None, meta=None, raises=None, supabase=None):
    """Return an ExitStack with all external calls patched."""
    if gemini_result is None:
        gemini_result = dict(_GEMINI_RESULT)

    def _gemini_fn(*_a, **_kw):
        if raises is not None:
            raise raises
        return gemini_result

    stack = ExitStack()
    stack.enter_context(patch("app.services.gemini.analyse", side_effect=_gemini_fn))
    stack.enter_context(
        patch("app.services.video_meta.extract_video_metadata", return_value=meta or _META)
    )
    mock_log = stack.enter_context(patch("app.services.usage_logger.log_usage"))
    mock_sb = supabase if supabase is not None else MagicMock()
    # _store_result now uses get_supabase_admin (service-role key) so it can
    # bypass RLS when writing on behalf of the authenticated user.
    stack.enter_context(patch("app.database.get_supabase_admin", return_value=mock_sb))
    # stash the mocks as attributes for assertions
    stack.mock_log = mock_log   # type: ignore[attr-defined]
    stack.mock_sb = mock_sb     # type: ignore[attr-defined]
    return stack


# ---------------------------------------------------------------------------
# Content-type and magic-byte validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_unsupported_content_type_rejected(self, client):
        with _patches():
            resp = _post(client, content_type="text/plain")
        assert resp.status_code == 400
        assert "video" in resp.json()["detail"].lower() or "image" in resp.json()["detail"].lower()

    def test_invalid_magic_bytes_rejected(self, client):
        with _patches():
            resp = _post(client, file_bytes=_JPEG_HEADER + b"\x00" * 100)
        assert resp.status_code == 400

    def test_file_too_large_rejected(self, client):
        """Patch the size cap to a tiny value so we don't need 350 MB of data."""
        with (
            _patches(),
            patch("app.routers.analyse._MAX_UPLOAD_BYTES", 10),
        ):
            resp = _post(client, file_bytes=_MP4_BYTES)
        assert resp.status_code == 413
        assert "upload limit" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_unauthenticated_returns_200(self, client):
        with _patches():
            resp = _post(client)
        assert resp.status_code == 200

    def test_response_includes_video_metadata(self, client):
        meta = {"duration_seconds": 12.3, "resolution": "1920x1080"}
        with _patches(meta=meta):
            resp = _post(client)
        body = resp.json()
        assert body["video_metadata"]["duration_seconds"] == 12.3
        assert body["video_metadata"]["resolution"] == "1920x1080"

    def test_token_usage_not_in_response(self, client):
        """_token_usage must be popped from the result before returning."""
        with _patches():
            resp = _post(client)
        assert "_token_usage" not in resp.json()

    def test_authenticated_calls_store_result(self, authed_client):
        mock_sb = MagicMock()
        with _patches(supabase=mock_sb):
            resp = _post(authed_client)
        assert resp.status_code == 200
        mock_sb.table.assert_called_with("videos")
        mock_sb.table.return_value.insert.assert_called_once()

    def test_unauthenticated_does_not_call_store_result(self, client):
        mock_sb = MagicMock()
        with _patches(supabase=mock_sb):
            resp = _post(client)
        assert resp.status_code == 200
        mock_sb.table.return_value.insert.assert_not_called()

    def test_log_usage_called_with_correct_args(self, client):
        with _patches() as stack:
            _post(client)
        stack.mock_log.assert_called_once_with(
            analysis_type="video",
            model="gemini-2.5-flash",
            user_id=None,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )


# ---------------------------------------------------------------------------
# Browser GPS fallback
# ---------------------------------------------------------------------------

class TestGpsFallback:
    def test_browser_gps_used_when_video_has_none(self, client):
        with _patches(meta={}):
            resp = client.post(
                "/analyse",
                files={"file": ("clip.mp4", _MP4_BYTES, "video/mp4")},
                data={"browser_lat": "51.5", "browser_lon": "-0.1"},
            )
        meta = resp.json()["video_metadata"]
        assert meta["latitude"] == pytest.approx(51.5)
        assert meta["longitude"] == pytest.approx(-0.1)
        assert meta["location_source"] == "browser"

    def test_browser_gps_not_used_when_video_has_gps(self, client):
        video_meta = {"latitude": 48.8566, "longitude": 2.3522, "location_source": "video"}
        with _patches(meta=video_meta):
            resp = client.post(
                "/analyse",
                files={"file": ("clip.mp4", _MP4_BYTES, "video/mp4")},
                data={"browser_lat": "51.5", "browser_lon": "-0.1"},
            )
        meta = resp.json()["video_metadata"]
        assert meta["latitude"] == pytest.approx(48.8566)   # video GPS preserved
        assert meta["location_source"] == "video"


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

class TestErrorMapping:
    def test_value_error_returns_422(self, client):
        with _patches(raises=ValueError("bad JSON")):
            resp = _post(client)
        assert resp.status_code == 422

    def test_quota_error_429(self, client):
        with _patches(raises=Exception("429 quota exceeded")):
            resp = _post(client)
        assert resp.status_code == 429

    def test_rate_limit_error_429(self, client):
        with _patches(raises=Exception("rate limit reached")):
            resp = _post(client)
        assert resp.status_code == 429

    def test_ratelimit_keyword_429(self, client):
        with _patches(raises=Exception("ratelimitError")):
            resp = _post(client)
        assert resp.status_code == 429

    def test_generic_exception_returns_500(self, client):
        with _patches(raises=Exception("internal crash")):
            resp = _post(client)
        assert resp.status_code == 500

    def test_500_detail_does_not_leak_internal_message(self, client):
        with _patches(raises=Exception("SECRET db-password")):
            resp = _post(client)
        assert "SECRET" not in resp.json()["detail"]
        assert "db-password" not in resp.json()["detail"]


# ---------------------------------------------------------------------------
# _assert_image_magic unit tests
# ---------------------------------------------------------------------------

class TestAssertImageMagic:
    def test_jpeg_accepted(self):
        _assert_image_magic(_JPEG_HEADER)

    def test_png_accepted(self):
        _assert_image_magic(_PNG_HEADER)

    def test_webp_accepted(self):
        _assert_image_magic(_WEBP_HEADER)

    def test_too_small_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _assert_image_magic(b"\xff\xd8")   # only 2 bytes
        assert exc_info.value.status_code == 400

    def test_unrecognised_bytes_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _assert_image_magic(_MP4_HEADER)   # video magic — not an image
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Image upload helpers
# ---------------------------------------------------------------------------

def _patches_image(photo_result=None, raises=None):
    """Return an ExitStack with photo_analyzer.analyse patched."""
    if photo_result is None:
        photo_result = dict(_PHOTO_RESULT)

    async def _photo_fn(*_a, **_kw):
        if raises is not None:
            raise raises
        return photo_result

    stack = ExitStack()
    stack.enter_context(patch("app.services.photo_analyzer.analyse", side_effect=_photo_fn))
    mock_log = stack.enter_context(patch("app.services.usage_logger.log_usage"))
    stack.mock_log = mock_log  # type: ignore[attr-defined]
    return stack


def _post_image(test_client, file_bytes=None, content_type="image/jpeg",
                filename="photo.jpg", description=None):
    data = {}
    if description is not None:
        data["description"] = description
    return test_client.post(
        "/analyse",
        files={"file": (filename, file_bytes or _JPEG_BYTES, content_type)},
        data=data,
    )


# ---------------------------------------------------------------------------
# Image endpoint tests
# ---------------------------------------------------------------------------

class TestImageUpload:
    def test_jpeg_upload_returns_200(self, client):
        with _patches_image():
            resp = _post_image(client)
        assert resp.status_code == 200
        assert resp.json()["likely_issue"] == "Dripping tap under the sink"

    def test_png_upload_returns_200(self, client):
        with _patches_image():
            resp = _post_image(client, file_bytes=_PNG_BYTES, content_type="image/png", filename="photo.png")
        assert resp.status_code == 200

    def test_webp_upload_returns_200(self, client):
        webp_bytes = _WEBP_HEADER + b"\x00" * 100
        with _patches_image():
            resp = _post_image(client, file_bytes=webp_bytes, content_type="image/webp", filename="photo.webp")
        assert resp.status_code == 200

    def test_description_forwarded_when_long_enough(self, client):
        captured = {}

        async def _capture(images, description, trade_category):
            captured["description"] = description
            return dict(_PHOTO_RESULT)

        with patch("app.services.photo_analyzer.analyse", side_effect=_capture), \
             patch("app.services.usage_logger.log_usage"):
            _post_image(client, description="Leaking pipe under kitchen sink")

        assert captured["description"] == "Leaking pipe under kitchen sink"

    def test_short_description_uses_fallback(self, client):
        captured = {}

        async def _capture(images, description, trade_category):
            captured["description"] = description
            return dict(_PHOTO_RESULT)

        with patch("app.services.photo_analyzer.analyse", side_effect=_capture), \
             patch("app.services.usage_logger.log_usage"):
            _post_image(client, description="fix")   # < 10 chars

        assert "Please analyse" in captured["description"]

    def test_missing_description_uses_fallback(self, client):
        captured = {}

        async def _capture(images, description, trade_category):
            captured["description"] = description
            return dict(_PHOTO_RESULT)

        with patch("app.services.photo_analyzer.analyse", side_effect=_capture), \
             patch("app.services.usage_logger.log_usage"):
            _post_image(client)   # no description

        assert len(captured["description"]) >= 10

    def test_invalid_image_magic_rejected(self, client):
        with _patches_image():
            resp = _post_image(client, file_bytes=_MP4_BYTES)   # MP4 bytes with image/jpeg content-type
        assert resp.status_code == 400

    def test_image_too_large_rejected(self, client):
        with _patches_image(), patch("app.routers.analyse._MAX_IMAGE_BYTES", 10):
            resp = _post_image(client)
        assert resp.status_code == 413

    def test_value_error_returns_422(self, client):
        with _patches_image(raises=ValueError("blurry")):
            resp = _post_image(client)
        assert resp.status_code == 422

    def test_quota_error_returns_429(self, client):
        with _patches_image(raises=Exception("429 quota exceeded")):
            resp = _post_image(client)
        assert resp.status_code == 429

    def test_rate_limit_error_returns_429(self, client):
        with _patches_image(raises=Exception("rate limit reached")):
            resp = _post_image(client)
        assert resp.status_code == 429

    def test_generic_error_returns_500(self, client):
        with _patches_image(raises=Exception("internal crash")):
            resp = _post_image(client)
        assert resp.status_code == 500

    def test_500_does_not_leak_detail(self, client):
        with _patches_image(raises=Exception("SECRET key")):
            resp = _post_image(client)
        assert "SECRET" not in resp.json()["detail"]

    def test_log_usage_called_with_photo_type(self, client):
        with _patches_image() as stack:
            _post_image(client)
        stack.mock_log.assert_called_once_with(
            analysis_type="photo",
            model="gemini-2.5-flash",
            user_id=None,
            prompt_tokens=80,
            completion_tokens=40,
            total_tokens=120,
        )
