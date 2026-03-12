"""
Unit tests for backend/app/services/photo_analyzer.py

Covers:
  - _sharpness_score()         — image clarity detection
  - _fetch_image_bytes()       — base64 data URI parsing
  - _load_and_preprocess()     — full per-image pipeline (validate → resize → flag)
  - analyse()                  — orchestrator (Gemini mocked)

Gemini is never called: _call_gemini is patched wherever the orchestrator
would invoke it, so no API key or network access is required.
"""

import asyncio
import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

# conftest.py sets GEMINI_API_KEY before this import
from app.services import photo_analyzer
from app.services.photo_analyzer import (
    _BLUR_THRESHOLD,
    _MAX_DIMENSION,
    _MIN_DIMENSION,
    _PreparedImage,
    _fetch_image_bytes,
    _load_and_preprocess,
    _sharpness_score,
)
from tests.conftest import (
    as_data_uri,
    make_checkerboard_jpeg,
    make_solid_png,
)


# ===========================================================================
# _sharpness_score
# ===========================================================================

class TestSharpnessScore:
    """_sharpness_score returns a float proxy for image clarity."""

    def test_solid_color_image_scores_near_zero(self):
        """A uniform solid-color image has no edges → score ≈ 0 → blurry."""
        img = Image.new("RGB", (200, 200), color=(128, 128, 128))
        score = _sharpness_score(img)
        assert score < _BLUR_THRESHOLD, (
            f"Expected solid image score < {_BLUR_THRESHOLD}, got {score:.2f}"
        )

    def test_checkerboard_image_scores_above_threshold(self):
        """A checkerboard has dense edges → score well above BLUR_THRESHOLD."""
        jpeg = make_checkerboard_jpeg(size=200, square=10)
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        score = _sharpness_score(img)
        assert score > _BLUR_THRESHOLD, (
            f"Expected checkerboard score > {_BLUR_THRESHOLD}, got {score:.2f}"
        )

    def test_returns_float(self):
        img = Image.new("RGB", (50, 50), color=(0, 0, 0))
        assert isinstance(_sharpness_score(img), float)


# ===========================================================================
# _fetch_image_bytes
# ===========================================================================

class TestFetchImageBytes:
    """_fetch_image_bytes decodes base64 data URIs and fetches HTTPS URLs."""

    async def test_valid_jpeg_data_uri(self):
        jpeg = make_checkerboard_jpeg(size=100)
        uri = as_data_uri(jpeg, mime="image/jpeg")
        result = await _fetch_image_bytes(uri)
        assert result == jpeg

    async def test_valid_png_data_uri(self):
        png = make_solid_png(width=100, height=100)
        uri = as_data_uri(png, mime="image/png")
        result = await _fetch_image_bytes(uri)
        assert result == png

    async def test_data_uri_whitespace_is_stripped(self):
        jpeg = make_checkerboard_jpeg(size=50)
        uri = "  " + as_data_uri(jpeg) + "  "
        result = await _fetch_image_bytes(uri)
        assert result == jpeg

    async def test_malformed_data_uri_raises(self):
        """data: prefix without a ;base64, marker → ValueError."""
        with pytest.raises(ValueError, match="Malformed data URI"):
            await _fetch_image_bytes("data:image/jpeg,notbase64here")

    async def test_invalid_base64_content_raises(self):
        with pytest.raises(ValueError, match="Base64 decode failed"):
            await _fetch_image_bytes("data:image/jpeg;base64,!!!not-valid-base64!!!")

    async def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError, match="HTTPS URL or a base64 data URI"):
            await _fetch_image_bytes("ftp://example.com/image.jpg")

    async def test_plain_filename_raises(self):
        with pytest.raises(ValueError, match="HTTPS URL or a base64 data URI"):
            await _fetch_image_bytes("photo.jpg")


# ===========================================================================
# _load_and_preprocess
# ===========================================================================

class TestLoadAndPreprocess:
    """
    _load_and_preprocess runs the full per-image pipeline:
    fetch → PIL open/verify → min-size guard → resize → sharpness → JPEG encode.
    """

    async def test_sharp_image_returns_ok_quality(self, sharp_data_uri):
        result = await _load_and_preprocess(0, sharp_data_uri)
        assert result.quality == "ok"
        assert result.pil_image is not None
        assert result.note is None

    async def test_blurry_image_flagged_but_still_usable(self, blurry_data_uri):
        """Blurry images are flagged but NOT discarded — the AI still receives them."""
        result = await _load_and_preprocess(0, blurry_data_uri)
        assert result.quality == "blurry"
        assert result.pil_image is not None  # still passed to Gemini
        assert "blurry" in (result.note or "").lower()

    async def test_tiny_image_rejected(self, tiny_data_uri):
        result = await _load_and_preprocess(0, tiny_data_uri)
        assert result.quality == "unsupported"
        assert result.pil_image is None
        assert "too small" in (result.note or "").lower()

    async def test_large_image_is_resized(self, large_data_uri):
        result = await _load_and_preprocess(0, large_data_uri)
        assert result.quality in ("ok", "blurry")   # checkerboard → ok
        assert result.pil_image is not None
        w, h = result.pil_image.size
        assert max(w, h) <= _MAX_DIMENSION, (
            f"Expected longest edge ≤ {_MAX_DIMENSION}, got {max(w, h)}"
        )

    async def test_corrupt_bytes_rejected(self, corrupt_data_uri):
        result = await _load_and_preprocess(0, corrupt_data_uri)
        assert result.quality == "unidentifiable"
        assert result.pil_image is None

    async def test_fetch_failure_returns_unsupported(self):
        """A bad URI (unsupported scheme) produces quality='unsupported'."""
        result = await _load_and_preprocess(0, "ftp://example.com/img.jpg")
        assert result.quality == "unsupported"
        assert result.pil_image is None
        assert result.note is not None

    async def test_jpeg_len_recorded(self, sharp_data_uri):
        result = await _load_and_preprocess(0, sharp_data_uri)
        assert result.jpeg_bytes_len > 0

    # --- Role assignment ---------------------------------------------------

    @pytest.mark.parametrize("index, expected_role", [
        (0, "Wide Shot"),
        (1, "Close-up"),
        (2, "Scale / Context"),
        (3, "Supplemental A"),
        (4, "Supplemental B"),
    ])
    async def test_role_assigned_by_position(self, index, expected_role, sharp_data_uri):
        result = await _load_and_preprocess(index, sharp_data_uri)
        assert result.role == expected_role

    async def test_role_capped_for_indices_beyond_five(self, sharp_data_uri):
        """Index 10 → last defined role (Supplemental B), not an IndexError."""
        result = await _load_and_preprocess(10, sharp_data_uri)
        assert result.role == "Supplemental B"


# ===========================================================================
# analyse()  (Gemini mocked)
# ===========================================================================

_GOOD_GEMINI_RESPONSE = {
    "likely_issue":        "Burst compression fitting on 15mm copper pipe",
    "urgency_score":       8,
    "required_tools":      ["adjustable spanner", "pipe cutter"],
    "estimated_parts":     ["15mm compression elbow"],
    "image_quality_notes": ["Image 1: clear"],
    "reasoning":           "Water visible around the joint indicates fitting failure.",
    "_token_usage": {
        "prompt_tokens":     1_200,
        "completion_tokens": 95,
        "total_tokens":      1_295,
    },
}


class TestAnalyse:
    """
    analyse() orchestrates load → preprocess → Gemini → shape result.
    _call_gemini is patched so no real API call is made.
    """

    async def test_all_bad_images_raise_value_error(self, corrupt_data_uri):
        with pytest.raises(ValueError, match="None of the supplied images"):
            await photo_analyzer.analyse(
                images=[corrupt_data_uri],
                description="Something is very wrong with my boiler",
                trade_category=None,
            )

    async def test_successful_analysis_returns_required_keys(self, sharp_data_uri):
        with patch("app.services.photo_analyzer._call_gemini",
                   return_value=dict(_GOOD_GEMINI_RESPONSE)):
            result = await photo_analyzer.analyse(
                images=[sharp_data_uri],
                description="My kitchen tap is dripping from the base",
                trade_category="plumbing",
            )

        assert set(result.keys()) == {
            "likely_issue", "urgency_score", "required_tools",
            "estimated_parts", "image_feedback", "token_usage_estimate",
        }

    async def test_reasoning_is_stripped_from_result(self, sharp_data_uri):
        with patch("app.services.photo_analyzer._call_gemini",
                   return_value=dict(_GOOD_GEMINI_RESPONSE)):
            result = await photo_analyzer.analyse(
                images=[sharp_data_uri],
                description="Damp patch on the ceiling, getting bigger",
                trade_category="damp",
            )
        assert "reasoning" not in result

    async def test_token_usage_is_in_result(self, sharp_data_uri):
        with patch("app.services.photo_analyzer._call_gemini",
                   return_value=dict(_GOOD_GEMINI_RESPONSE)):
            result = await photo_analyzer.analyse(
                images=[sharp_data_uri],
                description="Damp patch on the ceiling, getting bigger",
                trade_category=None,
            )
        usage = result["token_usage_estimate"]
        assert usage["prompt_tokens"]     == 1_200
        assert usage["completion_tokens"] == 95
        assert usage["total_tokens"]      == 1_295

    async def test_urgency_score_clamped_to_minimum(self, sharp_data_uri):
        low_score = dict(_GOOD_GEMINI_RESPONSE, urgency_score=0)
        with patch("app.services.photo_analyzer._call_gemini", return_value=low_score):
            result = await photo_analyzer.analyse(
                images=[sharp_data_uri],
                description="Hairline crack in plaster, very minor",
                trade_category="structural",
            )
        assert result["urgency_score"] == 1

    async def test_urgency_score_clamped_to_maximum(self, sharp_data_uri):
        high_score = dict(_GOOD_GEMINI_RESPONSE, urgency_score=99)
        with patch("app.services.photo_analyzer._call_gemini", return_value=high_score):
            result = await photo_analyzer.analyse(
                images=[sharp_data_uri],
                description="Sparks coming from the consumer unit",
                trade_category="electrical",
            )
        assert result["urgency_score"] == 10

    async def test_image_feedback_comes_from_preprocessing(self, sharp_data_uri):
        """image_feedback is built from preprocess output, not from Gemini's notes."""
        response_without_notes = dict(_GOOD_GEMINI_RESPONSE)
        response_without_notes.pop("image_quality_notes", None)

        with patch("app.services.photo_analyzer._call_gemini",
                   return_value=response_without_notes):
            result = await photo_analyzer.analyse(
                images=[sharp_data_uri],
                description="Water stain under the kitchen sink",
                trade_category="plumbing",
            )

        feedback = result["image_feedback"]
        assert len(feedback) == 1
        assert feedback[0]["index"] == 0
        assert feedback[0]["role"]  == "Wide Shot"
        assert feedback[0]["quality"] in ("ok", "blurry")

    async def test_multiple_images_all_appear_in_feedback(
        self, sharp_data_uri, blurry_data_uri
    ):
        with patch("app.services.photo_analyzer._call_gemini",
                   return_value=dict(_GOOD_GEMINI_RESPONSE)):
            result = await photo_analyzer.analyse(
                images=[sharp_data_uri, blurry_data_uri],
                description="Roof tile cracked, water coming through",
                trade_category="roofing",
            )

        assert len(result["image_feedback"]) == 2
        roles = {fb["role"] for fb in result["image_feedback"]}
        assert "Wide Shot" in roles
        assert "Close-up" in roles

    async def test_mixed_good_and_bad_images_succeeds(
        self, sharp_data_uri, corrupt_data_uri
    ):
        """One usable image + one corrupt → should succeed (not raise)."""
        with patch("app.services.photo_analyzer._call_gemini",
                   return_value=dict(_GOOD_GEMINI_RESPONSE)):
            result = await photo_analyzer.analyse(
                images=[corrupt_data_uri, sharp_data_uri],
                description="Radiator not heating up at all in the bedroom",
                trade_category="plumbing",
            )
        # Corrupt image should appear with quality != "ok"
        qualities = {fb["quality"] for fb in result["image_feedback"]}
        assert "unidentifiable" in qualities
        assert "ok" in qualities or "blurry" in qualities
