"""Unit tests for app/services/gemini.py.

google.generativeai is already stubbed in conftest.py.  We patch specific
attributes on that stub for each test so no real Gemini API calls are made.

Coverage
--------
- Happy path: upload → already ACTIVE → generate → parsed JSON returned
- Polling loop: PROCESSING → ACTIVE after one poll
- File enters FAILED state → RuntimeError raised
- Markdown fence stripping (```json...``` and plain ```)
- Token usage extracted from response.usage_metadata
- Token counts default to 0 when usage_metadata is absent
- Non-JSON Gemini response raises JSONDecodeError
- delete_file always called even when generation raises
- delete_file failure does not mask the original error
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import gemini as gemini_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(state_name: str, name: str = "files/abc123"):
    f = MagicMock()
    f.name = name
    f.state.name = state_name
    return f


def _make_response(text: str, prompt_tokens: int = 10, completion_tokens: int = 20):
    resp = MagicMock()
    resp.text = text
    resp.usage_metadata.prompt_token_count = prompt_tokens
    resp.usage_metadata.candidates_token_count = completion_tokens
    resp.usage_metadata.total_token_count = prompt_tokens + completion_tokens
    return resp


def _run(tmp_path, active_file, response):
    """Helper: patch genai, call analyse(), return result."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video bytes")

    with (
        patch("app.services.gemini.genai.upload_file", return_value=active_file),
        patch("app.services.gemini.genai.GenerativeModel") as mock_model_cls,
        patch("app.services.gemini.genai.delete_file"),
    ):
        mock_model_cls.return_value.generate_content.return_value = response
        return gemini_service.analyse(str(video), "video/mp4")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_returns_parsed_json(self, tmp_path):
        payload = {"problem_type": "plumbing", "urgency": "medium"}
        result = _run(tmp_path, _make_file("ACTIVE"), _make_response(json.dumps(payload)))
        assert result["problem_type"] == "plumbing"
        assert result["urgency"] == "medium"

    def test_token_usage_included(self, tmp_path):
        payload = {"problem_type": "electrical"}
        result = _run(
            tmp_path,
            _make_file("ACTIVE"),
            _make_response(json.dumps(payload), prompt_tokens=100, completion_tokens=50),
        )
        assert result["_token_usage"]["prompt_tokens"] == 100
        assert result["_token_usage"]["completion_tokens"] == 50
        assert result["_token_usage"]["total_tokens"] == 150

    def test_strips_json_code_fence(self, tmp_path):
        payload = {"problem_type": "damp"}
        fenced = f"```json\n{json.dumps(payload)}\n```"
        result = _run(tmp_path, _make_file("ACTIVE"), _make_response(fenced))
        assert result["problem_type"] == "damp"

    def test_strips_plain_code_fence(self, tmp_path):
        payload = {"urgency": "low"}
        fenced = f"```\n{json.dumps(payload)}\n```"
        result = _run(tmp_path, _make_file("ACTIVE"), _make_response(fenced))
        assert result["urgency"] == "low"

    def test_uses_gemini_2_5_flash_model(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        active_file = _make_file("ACTIVE")

        with (
            patch("app.services.gemini.genai.upload_file", return_value=active_file),
            patch("app.services.gemini.genai.GenerativeModel") as mock_model_cls,
            patch("app.services.gemini.genai.delete_file"),
        ):
            mock_model_cls.return_value.generate_content.return_value = _make_response('{"x": 1}')
            gemini_service.analyse(str(video), "video/mp4")
            mock_model_cls.assert_called_once_with("gemini-2.5-flash")

    def test_passes_mime_type_to_upload(self, tmp_path):
        video = tmp_path / "clip.webm"
        video.write_bytes(b"fake")
        active_file = _make_file("ACTIVE")

        with (
            patch("app.services.gemini.genai.upload_file", return_value=active_file) as mock_upload,
            patch("app.services.gemini.genai.GenerativeModel") as mock_model_cls,
            patch("app.services.gemini.genai.delete_file"),
        ):
            mock_model_cls.return_value.generate_content.return_value = _make_response('{"x": 1}')
            gemini_service.analyse(str(video), "video/webm")
            mock_upload.assert_called_once_with(str(video), mime_type="video/webm")


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

class TestPolling:
    def test_polls_until_active(self, tmp_path):
        """File starts PROCESSING → one poll → ACTIVE."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")

        processing = _make_file("PROCESSING", name="files/xyz")
        active = _make_file("ACTIVE", name="files/xyz")
        response = _make_response('{"problem_type": "general"}')

        with (
            patch("app.services.gemini.genai.upload_file", return_value=processing),
            patch("app.services.gemini.genai.get_file", return_value=active) as mock_get,
            patch("app.services.gemini.genai.GenerativeModel") as mock_model_cls,
            patch("app.services.gemini.genai.delete_file"),
            patch("app.services.gemini.time.sleep"),
        ):
            mock_model_cls.return_value.generate_content.return_value = response
            result = gemini_service.analyse(str(video), "video/mp4")

        assert result["problem_type"] == "general"
        mock_get.assert_called_once_with(processing.name)

    def test_file_failed_state_raises_runtime_error(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")

        with (
            patch("app.services.gemini.genai.upload_file", return_value=_make_file("FAILED")),
            patch("app.services.gemini.genai.delete_file"),
        ):
            with pytest.raises(RuntimeError, match="FAILED"):
                gemini_service.analyse(str(video), "video/mp4")


# ---------------------------------------------------------------------------
# Cleanup (finally block)
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_delete_file_always_called_on_success(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        active = _make_file("ACTIVE")

        with (
            patch("app.services.gemini.genai.upload_file", return_value=active),
            patch("app.services.gemini.genai.GenerativeModel") as mock_model_cls,
            patch("app.services.gemini.genai.delete_file") as mock_delete,
        ):
            mock_model_cls.return_value.generate_content.return_value = _make_response('{"x": 1}')
            gemini_service.analyse(str(video), "video/mp4")

        mock_delete.assert_called_once_with(active.name)

    def test_delete_file_called_even_when_generation_raises(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        active = _make_file("ACTIVE")

        with (
            patch("app.services.gemini.genai.upload_file", return_value=active),
            patch("app.services.gemini.genai.GenerativeModel") as mock_model_cls,
            patch("app.services.gemini.genai.delete_file") as mock_delete,
        ):
            mock_model_cls.return_value.generate_content.side_effect = RuntimeError("API error")
            with pytest.raises(RuntimeError):
                gemini_service.analyse(str(video), "video/mp4")

        mock_delete.assert_called_once()

    def test_delete_failure_does_not_mask_original_error(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        active = _make_file("ACTIVE")

        with (
            patch("app.services.gemini.genai.upload_file", return_value=active),
            patch("app.services.gemini.genai.GenerativeModel") as mock_model_cls,
            patch("app.services.gemini.genai.delete_file", side_effect=Exception("delete failed")),
        ):
            mock_model_cls.return_value.generate_content.side_effect = ValueError("original")
            with pytest.raises(ValueError, match="original"):
                gemini_service.analyse(str(video), "video/mp4")


# ---------------------------------------------------------------------------
# Token usage fallback
# ---------------------------------------------------------------------------

class TestTokenUsageFallback:
    def test_all_zero_when_no_usage_metadata(self, tmp_path):
        """response has no usage_metadata → all counts default to 0."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        active = _make_file("ACTIVE")

        # spec=[] means the mock has NO attributes → getattr returns 0 via getattr(usage, ..., 0)
        # but usage itself is None from getattr(response, "usage_metadata", None)
        resp = MagicMock(spec=["text"])
        resp.text = '{"problem_type": "plumbing"}'

        with (
            patch("app.services.gemini.genai.upload_file", return_value=active),
            patch("app.services.gemini.genai.GenerativeModel") as mock_model_cls,
            patch("app.services.gemini.genai.delete_file"),
        ):
            mock_model_cls.return_value.generate_content.return_value = resp
            result = gemini_service.analyse(str(video), "video/mp4")

        assert result["_token_usage"] == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }


# ---------------------------------------------------------------------------
# JSON parsing errors
# ---------------------------------------------------------------------------

class TestJsonParsing:
    def test_non_json_response_raises(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        active = _make_file("ACTIVE")
        resp = _make_response("Sorry, I cannot analyse that video.")

        with (
            patch("app.services.gemini.genai.upload_file", return_value=active),
            patch("app.services.gemini.genai.GenerativeModel") as mock_model_cls,
            patch("app.services.gemini.genai.delete_file"),
        ):
            mock_model_cls.return_value.generate_content.return_value = resp
            with pytest.raises(Exception):   # json.JSONDecodeError
                gemini_service.analyse(str(video), "video/mp4")
