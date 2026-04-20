"""
Tests for the document_verifier service.

Coverage
--------
verify_document()
  - insurance: required fields present → 'verified', expires_at set
  - insurance: all required fields null → 'needs_review'
  - licence: required fields present → 'verified'
  - certification: required fields present → 'verified'
  - other: holder_name present → 'verified'
  - other: holder_name null → 'needs_review'

_fetch_document_bytes()
  - valid base64 data URI → returns bytes
  - malformed data URI → ValueError
  - bad base64 payload → ValueError
  - unsupported scheme → ValueError

_open_and_prepare_image()
  - valid image → returns PIL Image
  - corrupt bytes → ValueError
  - too-small image → ValueError
  - oversized image → resized to ≤ _MAX_DIMENSION

_call_gemini()
  - valid JSON response → parsed dict
  - JSON with markdown fences → stripped and parsed
  - non-JSON response → ValueError

No real Gemini calls are made — genai is mocked via conftest.py sys.modules stubs.
"""

import base64
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.services.document_verifier import (
    VerificationResult,
    _call_gemini,
    _fetch_document_bytes,
    _open_and_prepare_image,
    verify_document,
)


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _make_jpeg_bytes(width: int = 300, height: int = 200) -> bytes:
    img = Image.new("RGB", (width, height), color=(180, 120, 60))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _as_data_uri(raw: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


# ---------------------------------------------------------------------------
# verify_document — happy paths
# ---------------------------------------------------------------------------

class TestVerifyDocumentHappyPaths:
    def _mock_gemini_result(self, extracted: dict):
        mock_img = MagicMock()
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps(extracted)
        mock_model.generate_content.return_value = mock_response
        return mock_model

    @pytest.mark.asyncio
    async def test_insurance_verified(self):
        extracted = {
            "insured_name":        "Acme Plumbing Ltd",
            "policy_number":       "POL-1234",
            "expiry_date":         "2027-06-30",
            "per_occurrence_limit": "£2,000,000",
            "insurer_name":        "SafeGuard Insurance",
        }
        img_bytes = _make_jpeg_bytes()
        uri       = _as_data_uri(img_bytes)

        with patch("app.services.document_verifier.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = self._mock_gemini_result(extracted)
            result = await verify_document(uri, "insurance")

        assert isinstance(result, VerificationResult)
        assert result.status == "verified"
        assert result.extracted_data["insured_name"] == "Acme Plumbing Ltd"
        assert result.expires_at == "2027-06-30"
        assert result.verification_notes is None

    @pytest.mark.asyncio
    async def test_insurance_needs_review_when_required_fields_null(self):
        extracted = {
            "insured_name":        None,
            "policy_number":       None,
            "expiry_date":         None,
            "per_occurrence_limit": None,
            "insurer_name":        None,
        }
        img_bytes = _make_jpeg_bytes()
        uri       = _as_data_uri(img_bytes)

        with patch("app.services.document_verifier.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = self._mock_gemini_result(extracted)
            result = await verify_document(uri, "insurance")

        assert result.status == "needs_review"
        assert result.verification_notes is not None
        assert result.expires_at is None

    @pytest.mark.asyncio
    async def test_licence_verified(self):
        extracted = {
            "holder_name":      "Jane Smith",
            "licence_number":   "GAS-5678",
            "trade_type":       "Gas Safe Engineer",
            "issuing_authority": "Gas Safe Register",
            "expiry_date":      "2028-03-15",
        }
        img_bytes = _make_jpeg_bytes()
        uri       = _as_data_uri(img_bytes)

        with patch("app.services.document_verifier.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = self._mock_gemini_result(extracted)
            result = await verify_document(uri, "licence")

        assert result.status == "verified"
        assert result.extracted_data["licence_number"] == "GAS-5678"
        assert result.expires_at == "2028-03-15"

    @pytest.mark.asyncio
    async def test_certification_verified(self):
        extracted = {
            "holder_name":          "Bob Builder",
            "certification_number": "CERT-9999",
            "certification_name":   "NVQ Level 3 Bricklaying",
            "issuing_body":         "City & Guilds",
            "expiry_date":          None,
        }
        img_bytes = _make_jpeg_bytes()
        uri       = _as_data_uri(img_bytes)

        with patch("app.services.document_verifier.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = self._mock_gemini_result(extracted)
            result = await verify_document(uri, "certification")

        assert result.status == "verified"
        assert result.expires_at is None  # no expiry on this cert

    @pytest.mark.asyncio
    async def test_other_verified(self):
        extracted = {
            "document_title":   "Proof of Identity",
            "holder_name":      "Alice Cooper",
            "issuing_authority": "DVLA",
            "expiry_date":      "2030-01-01",
            "reference_number": "REF-ABC",
        }
        img_bytes = _make_jpeg_bytes()
        uri       = _as_data_uri(img_bytes)

        with patch("app.services.document_verifier.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = self._mock_gemini_result(extracted)
            result = await verify_document(uri, "other")

        assert result.status == "verified"
        assert result.extracted_data["holder_name"] == "Alice Cooper"

    @pytest.mark.asyncio
    async def test_other_needs_review_when_holder_null(self):
        extracted = {"document_title": None, "holder_name": None, "issuing_authority": None,
                     "expiry_date": None, "reference_number": None}
        img_bytes = _make_jpeg_bytes()
        uri       = _as_data_uri(img_bytes)

        with patch("app.services.document_verifier.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = self._mock_gemini_result(extracted)
            result = await verify_document(uri, "other")

        assert result.status == "needs_review"


# ---------------------------------------------------------------------------
# _fetch_document_bytes
# ---------------------------------------------------------------------------

class TestFetchDocumentBytes:
    @pytest.mark.asyncio
    async def test_valid_data_uri(self):
        raw = b"hello document"
        uri = _as_data_uri(raw)
        result = await _fetch_document_bytes(uri)
        assert result == raw

    @pytest.mark.asyncio
    async def test_malformed_data_uri_raises(self):
        with pytest.raises(ValueError, match="Malformed data URI"):
            await _fetch_document_bytes("data:image/jpeg;notbase64")

    @pytest.mark.asyncio
    async def test_bad_base64_raises(self):
        with pytest.raises(ValueError, match="Base64 decode failed"):
            await _fetch_document_bytes("data:image/jpeg;base64,!!!invalid!!!")

    @pytest.mark.asyncio
    async def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError, match="HTTPS URL or a base64 data URI"):
            await _fetch_document_bytes("ftp://example.com/doc.pdf")

    @pytest.mark.asyncio
    async def test_plain_string_raises(self):
        with pytest.raises(ValueError, match="HTTPS URL or a base64 data URI"):
            await _fetch_document_bytes("just a random string")


# ---------------------------------------------------------------------------
# _open_and_prepare_image
# ---------------------------------------------------------------------------

class TestOpenAndPrepareImage:
    def test_valid_jpeg_returns_pil_image(self):
        raw = _make_jpeg_bytes(400, 300)
        img = _open_and_prepare_image(raw)
        assert isinstance(img, Image.Image)

    def test_corrupt_bytes_raises(self):
        with pytest.raises(ValueError, match="corrupt or in an unsupported format"):
            _open_and_prepare_image(b"\x00\xFF\xAB\xCD" * 100)

    def test_too_small_raises(self):
        tiny = Image.new("RGB", (30, 30), "red")
        buf  = io.BytesIO()
        tiny.save(buf, format="JPEG")
        with pytest.raises(ValueError, match="too small"):
            _open_and_prepare_image(buf.getvalue())

    def test_oversized_image_is_resized(self):
        raw = _make_jpeg_bytes(width=3000, height=2000)
        img = _open_and_prepare_image(raw)
        assert max(img.size) <= 2_000

    def test_valid_png_accepted(self):
        png = Image.new("RGB", (200, 200), "blue")
        buf = io.BytesIO()
        png.save(buf, format="PNG")
        img = _open_and_prepare_image(buf.getvalue())
        assert isinstance(img, Image.Image)


# ---------------------------------------------------------------------------
# _call_gemini
# ---------------------------------------------------------------------------

class TestCallGemini:
    def _make_model(self, response_text: str) -> MagicMock:
        model    = MagicMock()
        response = MagicMock()
        response.text = response_text
        model.generate_content.return_value = response
        return model

    def test_valid_json_response(self):
        payload = {"insured_name": "Test Co", "expiry_date": "2027-01-01"}
        model   = self._make_model(json.dumps(payload))
        img     = MagicMock()

        with patch("app.services.document_verifier.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = model
            result = _call_gemini(img, "insurance")

        assert result["insured_name"] == "Test Co"
        assert result["expiry_date"] == "2027-01-01"

    def test_markdown_fences_are_stripped(self):
        payload = {"licence_number": "LIC-001", "expiry_date": "2028-05-01"}
        fenced  = f"```json\n{json.dumps(payload)}\n```"
        model   = self._make_model(fenced)
        img     = MagicMock()

        with patch("app.services.document_verifier.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = model
            result = _call_gemini(img, "licence")

        assert result["licence_number"] == "LIC-001"

    def test_non_json_raises_value_error(self):
        model = self._make_model("I cannot read this document.")
        img   = MagicMock()

        with patch("app.services.document_verifier.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = model
            with pytest.raises(ValueError, match="non-JSON response"):
                _call_gemini(img, "insurance")

    def test_correct_prompt_used_for_each_type(self):
        payload = {"holder_name": "Alice", "expiry_date": "2029-01-01",
                   "certification_number": "C-123", "certification_name": "NVQ",
                   "issuing_body": "C&G"}
        model = self._make_model(json.dumps(payload))
        img   = MagicMock()

        with patch("app.services.document_verifier.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = model
            _call_gemini(img, "certification")

        call_args = model.generate_content.call_args[0][0]
        prompt_text = call_args[0]
        assert "certification" in prompt_text.lower()
