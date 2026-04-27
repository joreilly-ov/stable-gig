"""TradePhotoAnalyzer — static-image analysis service.

Entirely separate from the video analysis pipeline (gemini.py).

Flow:
  1. Load each image source (HTTPS URL or base64 data URI) concurrently.
  2. Validate, resize, re-encode as JPEG — minimise token cost without losing
     diagnostic detail.
  3. Run a per-image sharpness check and flag blurry / unusable images.
  4. Build a Multi-Perspective Triangulation prompt that assigns each image a
     positional role (Wide Shot → Close-up → Scale/Context → Supplemental).
  5. Call Gemini 2.5 Flash via asyncio.to_thread (SDK is synchronous).
  6. Parse and return the structured result.
"""

import asyncio
import base64
import io
import json
import logging
import re
import statistics
from dataclasses import dataclass
from typing import Literal

import httpx
from PIL import Image, ImageFilter
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import google.generativeai as genai
from app.config import settings
from app.services.vertical_config import get_vertical_config

genai.configure(api_key=settings.gemini_api_key)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pre-processing constants
# ---------------------------------------------------------------------------
_MAX_DIMENSION   = 1_200            # px; cap the longer edge to limit token usage
_JPEG_QUALITY    = 82               # JPEG quality — preserves diagnostic detail at ~60 % size
_MIN_DIMENSION   = 80               # px; anything smaller is a thumbnail or corrupt
_BLUR_THRESHOLD  = 6.0              # mean edge-filter intensity; below this → blurry flag
_MAX_FETCH_BYTES = 20 * 1_024 * 1_024  # 20 MB hard cap per URL fetch

# Image roles are sourced from the active vertical config at call time.

# ---------------------------------------------------------------------------
# Internal dataclass
# ---------------------------------------------------------------------------
ImageQuality = Literal["ok", "blurry", "unidentifiable", "unsupported"]


@dataclass
class _PreparedImage:
    index:            int
    quality:          ImageQuality
    note:             str | None
    pil_image:        Image.Image | None  # None when unusable
    jpeg_bytes_len:   int                 # byte size post-resize (for cost logging)
    role:             str
    role_instruction: str


# ---------------------------------------------------------------------------
# Prompt templates — static portion (schema instruction shared across verticals)
# ---------------------------------------------------------------------------

_JSON_INSTRUCTION = """
Based on all images and the customer description, return ONLY a valid JSON object — \
no markdown fences, no commentary — in exactly this schema:

{
  "likely_issue":          "<concise one-sentence diagnosis>",
  "urgency_score":         <integer 1–10; 1 = cosmetic, 10 = immediate safety risk>,
  "required_tools":        ["<specific tool>", "..."],
  "estimated_parts":       ["<part with size/spec where visible>", "..."],
  "image_quality_notes":   ["Image 1: <observation>", "..."],
  "reasoning":             "<internal chain-of-thought — max 3 sentences>"
}

Rules:
- urgency_score MUST be an integer, not a string.
- required_tools must be specific (e.g. "½-inch basin wrench", not just "wrench").
- estimated_parts must include sizes/specs where visible (e.g. "22mm compression elbow").
- If an image is blurry or unavailable, note it in image_quality_notes and rely on the others.
- If NO image provides enough information, set likely_issue to "INSUFFICIENT_EVIDENCE" \
and explain in reasoning what additional photographs are needed.
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
async def analyse(
    images:         list[str],
    description:    str,
    trade_category: str | None,
) -> dict:
    """
    Orchestrate the full analysis pipeline and return a dict shaped to match
    PhotoAnalysisResponse.  Raises ValueError on unrecoverable input errors.
    """
    prepared = await _load_and_preprocess_all(images)

    usable = [p for p in prepared if p.pil_image is not None]
    if not usable:
        feedback = [
            {"index": p.index, "role": p.role, "quality": p.quality, "note": p.note}
            for p in prepared
        ]
        raise ValueError(
            "None of the supplied images could be processed. "
            "Please provide clear, well-lit photographs (JPEG, PNG, or WebP). "
            f"Image feedback: {feedback}"
        )

    # Gemini SDK is synchronous — run in a thread pool to avoid blocking the event loop.
    gemini_raw = await asyncio.to_thread(
        _call_gemini, prepared, description, trade_category
    )

    # --- Pull internal-only fields before returning --------------------
    reasoning     = gemini_raw.pop("reasoning", "")
    token_usage   = gemini_raw.pop("_token_usage", {})
    # (image_quality_notes from Gemini are already logged inside _call_gemini)

    log.info(
        "photo_analysis_complete",
        extra={
            "usable_images": len(usable),
            "total_images":  len(prepared),
            "token_usage":   token_usage,
            "reasoning":     reasoning,  # internal — not returned to caller
        },
    )

    # --- Build per-image feedback from preprocessing (deterministic) ---
    image_feedback = [
        {"index": p.index, "role": p.role, "quality": p.quality, "note": p.note}
        for p in prepared
    ]

    return {
        "likely_issue":         gemini_raw.get("likely_issue", "Unknown"),
        "urgency_score":        max(1, min(10, int(gemini_raw.get("urgency_score", 1)))),
        "required_tools":       gemini_raw.get("required_tools", []),
        "estimated_parts":      gemini_raw.get("estimated_parts", []),
        "image_feedback":       image_feedback,
        "token_usage_estimate": token_usage,
    }


# ---------------------------------------------------------------------------
# Image loading + preprocessing
# ---------------------------------------------------------------------------
async def _load_and_preprocess_all(sources: list[str]) -> list[_PreparedImage]:
    """Load and preprocess all image sources concurrently."""
    tasks = [_load_and_preprocess(i, src) for i, src in enumerate(sources)]
    return list(await asyncio.gather(*tasks))


async def _load_and_preprocess(index: int, source: str) -> _PreparedImage:
    image_roles = get_vertical_config()["image_roles"]
    role, instruction = image_roles[min(index, len(image_roles) - 1)]

    # 1. Fetch raw bytes ------------------------------------------------
    try:
        raw_bytes = await _fetch_image_bytes(source)
    except Exception as exc:
        log.warning("photo_fetch_failed", extra={"index": index, "error": str(exc)})
        return _PreparedImage(
            index=index, quality="unsupported",
            note=f"Could not load image: {exc}",
            pil_image=None, jpeg_bytes_len=0,
            role=role, role_instruction=instruction,
        )

    # 2. Open and validate with PIL -------------------------------------
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img.verify()                          # detects truncated files
        img = Image.open(io.BytesIO(raw_bytes))  # must reopen after verify()
        img = img.convert("RGB")              # normalise — drops alpha, fixes palette mode
    except Exception as exc:
        log.warning("photo_open_failed", extra={"index": index, "error": str(exc)})
        return _PreparedImage(
            index=index, quality="unidentifiable",
            note="Image file is corrupt or not a recognised format (JPEG / PNG / WebP).",
            pil_image=None, jpeg_bytes_len=0,
            role=role, role_instruction=instruction,
        )

    # 3. Minimum size guard --------------------------------------------
    w, h = img.size
    if w < _MIN_DIMENSION or h < _MIN_DIMENSION:
        return _PreparedImage(
            index=index, quality="unsupported",
            note=f"Image is too small ({w}×{h} px). Please provide a higher-resolution photo.",
            pil_image=None, jpeg_bytes_len=0,
            role=role, role_instruction=instruction,
        )

    # 4. Resize — cap the longest edge to _MAX_DIMENSION ---------------
    if max(w, h) > _MAX_DIMENSION:
        scale = _MAX_DIMENSION / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # 5. Sharpness check -----------------------------------------------
    quality: ImageQuality = "ok"
    note:    str | None   = None
    score = _sharpness_score(img)
    if score < _BLUR_THRESHOLD:
        quality = "blurry"
        note = (
            f"Image appears blurry (sharpness score {score:.1f}). "
            "Diagnosis from this image may be limited — retake if possible."
        )
        log.info("photo_blurry", extra={"index": index, "sharpness_score": round(score, 2)})

    # 6. Re-encode as JPEG for consistent mime type + compression ------
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    jpeg_len = len(buf.getvalue())
    # Replace pil_image with the re-opened JPEG so Gemini gets the final version
    img = Image.open(buf)

    return _PreparedImage(
        index=index, quality=quality, note=note,
        pil_image=img, jpeg_bytes_len=jpeg_len,
        role=role, role_instruction=instruction,
    )


async def _fetch_image_bytes(source: str) -> bytes:
    """Return raw bytes from a base64 data URI or an HTTPS URL."""
    source = source.strip()

    # Base64 data URI: data:image/jpeg;base64,/9j/...
    if source.startswith("data:"):
        match = re.match(r"data:[^;]+;base64,(.+)", source, re.DOTALL)
        if not match:
            raise ValueError("Malformed data URI — expected: data:<mime>;base64,<data>")
        try:
            return base64.b64decode(match.group(1))
        except Exception as exc:
            raise ValueError(f"Base64 decode failed: {exc}") from exc

    # HTTP(S) URL — stream with a hard size cap
    if source.startswith(("http://", "https://")):
        # Note: SSRF risk — restrict to public internet IPs in a stricter deployment.
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            chunks: list[bytes] = []
            total = 0
            async with client.stream("GET", source) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1_024):
                    total += len(chunk)
                    if total > _MAX_FETCH_BYTES:
                        limit_mb = _MAX_FETCH_BYTES // 1_024 // 1_024
                        raise ValueError(
                            f"Image URL exceeds the {limit_mb} MB per-image size limit"
                        )
                    chunks.append(chunk)
        return b"".join(chunks)

    raise ValueError(
        "Each image must be an HTTPS URL or a base64 data URI (data:image/...;base64,...)"
    )


# ---------------------------------------------------------------------------
# Sharpness detection
# ---------------------------------------------------------------------------
def _sharpness_score(img: Image.Image) -> float:
    """
    Proxy for sharpness: mean pixel intensity of a FIND_EDGES-filtered grayscale image.
    Higher value = more edge detail = sharper image.
    A clear photo typically scores >10; blurry photos score <6.
    """
    gray   = img.convert("L").filter(ImageFilter.FIND_EDGES)
    pixels = list(gray.getdata())
    return statistics.fmean(pixels) if pixels else 0.0


# ---------------------------------------------------------------------------
# Gemini call  (synchronous — run via asyncio.to_thread)
# ---------------------------------------------------------------------------
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((Exception,)),  # Retry on any exception; let Gemini SDK determine fatal vs transient
    reraise=True,
)
def _call_gemini(
    prepared:       list[_PreparedImage],
    description:    str,
    trade_category: str | None,
) -> dict:
    """
    Build a Multi-Perspective Triangulation prompt, call Gemini 1.5 Flash,
    and return the parsed JSON dict (with _token_usage injected).
    
    Uses exponential backoff retry (3 attempts, 2–10 second delays) to handle
    transient API failures gracefully. Fatal errors (e.g., quota exceeded, invalid input)
    are re-raised immediately.
    """
    category_hint = (
        f"\nThe customer has categorised this as a '{trade_category}' issue."
        if trade_category
        else ""
    )

    system_intro = get_vertical_config()["system_intro"]

    # Build the content list: text role header → PIL image → repeat
    content: list = [
        system_intro.format(description=description, category_hint=category_hint)
    ]

    for p in prepared:
        header = f"\n[IMAGE {p.index + 1} — {p.role}]\n{p.role_instruction}"
        if p.quality != "ok":
            header += f"\n⚠ Quality flag: {p.quality}. {p.note or ''}"
        content.append(header)

        if p.pil_image is not None:
            content.append(p.pil_image)
        else:
            content.append("[Image unavailable — see quality flag above]")

    content.append(_JSON_INSTRUCTION)

    model    = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(content)

    raw = response.text.strip()
    # Strip any accidental markdown fences Gemini sometimes adds
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$",           "", raw).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        preview = raw[:400]
        raise ValueError(
            f"Gemini returned non-JSON response: {exc}\nRaw (first 400 chars): {preview}"
        ) from exc

    # Log Gemini's own quality observations internally
    if quality_notes := parsed.get("image_quality_notes"):
        log.info("photo_gemini_quality_notes", extra={"notes": quality_notes})

    # Attach token counts from the response metadata
    usage = getattr(response, "usage_metadata", None)
    parsed["_token_usage"] = {
        "prompt_tokens":     getattr(usage, "prompt_token_count",     0),
        "completion_tokens": getattr(usage, "candidates_token_count", 0),
        "total_tokens":      getattr(usage, "total_token_count",      0),
    }

    return parsed
