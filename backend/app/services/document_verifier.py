"""Contractor document verification service.

Uses Gemini 2.5 Flash vision to extract structured fields from contractor
documents (insurance certificates, trade licences, certifications).

Extraction schemas by document_type
--------------------------------------
insurance     : insured_name, policy_number, expiry_date, per_occurrence_limit, insurer_name
licence       : holder_name, licence_number, trade_type, issuing_authority, expiry_date
certification : holder_name, certification_number, certification_name, issuing_body, expiry_date
other         : document_title, holder_name, issuing_authority, expiry_date, reference_number

Auto-verification logic
-----------------------
A document is marked 'verified' if at least one required field is non-null.
Required fields per type:
  insurance     → insured_name, expiry_date
  licence       → licence_number, expiry_date
  certification → certification_number, expiry_date
  other         → holder_name

If all required fields are null the status is set to 'needs_review'.
"""

import asyncio
import base64
import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

import httpx
from PIL import Image
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import google.generativeai as genai
from app.config import settings

genai.configure(api_key=settings.gemini_api_key)

log = logging.getLogger(__name__)

DocumentType = Literal["insurance", "licence", "certification", "other"]

_MAX_FETCH_BYTES = 20 * 1_024 * 1_024  # 20 MB per document
_MAX_DIMENSION   = 2_000               # px — keep text readable for OCR
_JPEG_QUALITY    = 90                  # High quality preserves text detail

# Fields that must not all be null for auto-verification to succeed
_REQUIRED_FIELDS: dict[str, list[str]] = {
    "insurance":     ["insured_name", "expiry_date"],
    "licence":       ["licence_number", "expiry_date"],
    "certification": ["certification_number", "expiry_date"],
    "other":         ["holder_name"],
}

_EXTRACTION_PROMPTS: dict[str, str] = {
    "insurance": (
        "You are an insurance verification expert. Analyse this Certificate of Insurance. "
        "Extract the following fields into a JSON object:\n"
        "- insured_name: the name of the insured business or individual\n"
        "- policy_number: the policy reference number\n"
        "- expiry_date: the General Liability policy expiration date in ISO 8601 format (YYYY-MM-DD)\n"
        "- per_occurrence_limit: the per-occurrence coverage limit as a string (e.g. '£2,000,000')\n"
        "- insurer_name: the name of the insurance company\n\n"
        "If any field is illegible or not present, return null for that field. "
        "Return ONLY a valid JSON object — no markdown fences, no commentary."
    ),
    "licence": (
        "You are a licensing verification expert. Analyse this trade or professional licence. "
        "Extract the following fields into a JSON object:\n"
        "- holder_name: the full name of the licence holder\n"
        "- licence_number: the licence or registration number\n"
        "- trade_type: the trade or profession covered (e.g. 'Electrician', 'Gas Safe Engineer')\n"
        "- issuing_authority: the body that issued the licence\n"
        "- expiry_date: the expiration date in ISO 8601 format (YYYY-MM-DD)\n\n"
        "If any field is illegible or not present, return null for that field. "
        "Return ONLY a valid JSON object — no markdown fences, no commentary."
    ),
    "certification": (
        "You are a certification verification expert. Analyse this trade certification or qualification. "
        "Extract the following fields into a JSON object:\n"
        "- holder_name: the full name of the certificate holder\n"
        "- certification_number: the certificate or registration number\n"
        "- certification_name: the name of the qualification or certification\n"
        "- issuing_body: the organisation that issued the certification\n"
        "- expiry_date: the expiration date in ISO 8601 format (YYYY-MM-DD), or null if no expiry\n\n"
        "If any field is illegible or not present, return null for that field. "
        "Return ONLY a valid JSON object — no markdown fences, no commentary."
    ),
    "other": (
        "You are a document verification expert. Analyse this official document. "
        "Extract the following fields into a JSON object:\n"
        "- document_title: the title or type of the document\n"
        "- holder_name: the full name of the document holder or subject\n"
        "- issuing_authority: the organisation or authority that issued it\n"
        "- expiry_date: the expiration date in ISO 8601 format (YYYY-MM-DD), or null if no expiry\n"
        "- reference_number: any reference, registration, or document number\n\n"
        "If any field is illegible or not present, return null for that field. "
        "Return ONLY a valid JSON object — no markdown fences, no commentary."
    ),
}


@dataclass
class VerificationResult:
    status:             Literal["verified", "needs_review"]
    extracted_data:     dict
    verification_notes: str | None
    expires_at:         str | None  # ISO 8601 date string, e.g. "2026-12-31"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def verify_document(
    image_source:  str,
    document_type: DocumentType,
) -> VerificationResult:
    """Load, preprocess, and AI-extract fields from a contractor document.

    Returns a VerificationResult.  Raises ValueError on unrecoverable input
    errors (corrupt image, malformed URI, image too small).
    """
    raw_bytes = await _fetch_document_bytes(image_source)
    img       = _open_and_prepare_image(raw_bytes)

    extracted = await asyncio.to_thread(_call_gemini, img, document_type)

    required  = _REQUIRED_FIELDS.get(document_type, [])
    all_null  = all(extracted.get(f) is None for f in required)
    status    = "needs_review" if all_null else "verified"
    notes     = (
        "Required fields could not be extracted from the document image."
        if all_null else None
    )
    expires_at = extracted.get("expiry_date")

    log.info(
        "document_verified",
        extra={
            "document_type": document_type,
            "status":        status,
            "expires_at":    expires_at,
        },
    )

    return VerificationResult(
        status=status,
        extracted_data=extracted,
        verification_notes=notes,
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# Document fetching
# ---------------------------------------------------------------------------

async def _fetch_document_bytes(source: str) -> bytes:
    """Return raw bytes from a base64 data URI or an HTTPS URL."""
    source = source.strip()

    if source.startswith("data:"):
        match = re.match(r"data:[^;]+;base64,(.+)", source, re.DOTALL)
        if not match:
            raise ValueError("Malformed data URI — expected: data:<mime>;base64,<data>")
        try:
            return base64.b64decode(match.group(1))
        except Exception as exc:
            raise ValueError(f"Base64 decode failed: {exc}") from exc

    if source.startswith(("http://", "https://")):
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            chunks: list[bytes] = []
            total = 0
            async with client.stream("GET", source) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1_024):
                    total += len(chunk)
                    if total > _MAX_FETCH_BYTES:
                        limit_mb = _MAX_FETCH_BYTES // 1_024 // 1_024
                        raise ValueError(
                            f"Document URL exceeds the {limit_mb} MB size limit"
                        )
                    chunks.append(chunk)
        return b"".join(chunks)

    raise ValueError(
        "Document must be an HTTPS URL or a base64 data URI (data:image/...;base64,...)"
    )


# ---------------------------------------------------------------------------
# Image preparation
# ---------------------------------------------------------------------------

def _open_and_prepare_image(raw_bytes: bytes) -> Image.Image:
    """Open, validate, resize if needed, and return a PIL Image for Gemini."""
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img.verify()
        img = Image.open(io.BytesIO(raw_bytes))  # must reopen after verify()
        img = img.convert("RGB")
    except Exception as exc:
        raise ValueError(
            f"Document image is corrupt or in an unsupported format: {exc}"
        ) from exc

    w, h = img.size
    if w < 50 or h < 50:
        raise ValueError(
            f"Document image is too small ({w}×{h} px). Please provide a higher-resolution scan."
        )

    if max(w, h) > _MAX_DIMENSION:
        scale = _MAX_DIMENSION / max(w, h)
        img   = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    return Image.open(buf)


# ---------------------------------------------------------------------------
# Gemini call  (synchronous — run via asyncio.to_thread)
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_gemini(img: Image.Image, document_type: DocumentType) -> dict:
    """Submit the document image to Gemini 2.5 Flash and parse the JSON response."""
    prompt   = _EXTRACTION_PROMPTS[document_type]
    model    = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content([prompt, img])

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$",           "", raw).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        preview = raw[:400]
        raise ValueError(
            f"Gemini returned non-JSON response: {exc}\nRaw (first 400 chars): {preview}"
        ) from exc
