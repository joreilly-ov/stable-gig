"""POST /analyse/photos — TradePhotoAnalyzer endpoint.

Accepts 1–5 images (HTTPS URLs or base64 data URIs) plus a customer description,
runs Multi-Perspective Triangulation via Gemini 1.5 Flash, and returns a
structured diagnostic assessment.

Entirely separate from the /analyse (video) pipeline.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.dependencies import get_optional_user
from app.services import photo_analyzer

router = APIRouter(tags=["photo_analysis"])
log    = logging.getLogger(__name__)

_VALID_CATEGORIES = {"plumbing", "electrical", "structural", "damp", "roofing", "general"}


# ---------------------------------------------------------------------------
# Request / Response models
# Co-located here to keep this module self-contained — no coupling to schemas.py
# ---------------------------------------------------------------------------

class PhotoAnalysisRequest(BaseModel):
    images: list[str] = Field(
        ...,
        min_length=1,
        max_length=5,
        description=(
            "1–5 image sources. Each must be an HTTPS URL or a base64 data URI "
            "(data:image/<type>;base64,<data>). Supported formats: JPEG, PNG, WebP."
        ),
    )
    description: str = Field(
        ...,
        min_length=10,
        max_length=1_000,
        description="Brief customer description of the problem (10–1000 characters).",
    )
    trade_category: str | None = Field(
        default=None,
        description=(
            f"Optional trade hint. Must be one of: {sorted(_VALID_CATEGORIES)}. "
            "Omit if unknown."
        ),
    )

    @field_validator("trade_category")
    @classmethod
    def _validate_category(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_CATEGORIES:
            raise ValueError(
                f"trade_category must be one of {sorted(_VALID_CATEGORIES)}"
            )
        return v


class ImageFeedback(BaseModel):
    """Quality assessment for a single submitted image."""
    index:   int
    role:    str = Field(description="Triangulation role assigned to this image.")
    quality: Literal["ok", "blurry", "unidentifiable", "unsupported"]
    note:    str | None = Field(
        default=None,
        description="Human-readable explanation when quality != 'ok'.",
    )


class TokenUsage(BaseModel):
    """Actual token counts from Gemini — use to track cost efficiency."""
    prompt_tokens:     int
    completion_tokens: int
    total_tokens:      int


class PhotoAnalysisResponse(BaseModel):
    likely_issue: str = Field(
        description=(
            "Concise one-sentence diagnosis. Set to 'INSUFFICIENT_EVIDENCE' when "
            "the images do not provide enough detail for a confident diagnosis."
        )
    )
    urgency_score: int = Field(
        ge=1, le=10,
        description="1 = cosmetic / can wait, 10 = immediate safety risk.",
    )
    required_tools: list[str] = Field(
        description="Specific tools needed for the repair."
    )
    estimated_parts: list[str] = Field(
        description="Parts or materials identified (with sizes/specs where visible).",
    )
    image_feedback: list[ImageFeedback] = Field(
        description="Per-image quality flags from the preprocessing stage.",
    )
    token_usage_estimate: TokenUsage = Field(
        description="Actual Gemini token counts — zero when the API does not return them.",
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/analyse/photos",
    response_model=PhotoAnalysisResponse,
    summary="Analyse trade issue from photos",
    description=(
        "Submit 1–5 photographs of a home repair problem. The service assigns each image "
        "a triangulation role (Wide Shot, Close-up, Scale/Context …), preprocesses them "
        "for token efficiency, and returns a structured Gemini 1.5 Flash diagnosis."
    ),
)
async def analyse_photos(
    body: PhotoAnalysisRequest,
    user=Depends(get_optional_user),
):
    user_id = str(user.id) if user else None

    log.info(
        "photo_analysis_request",
        extra={
            "user_id":        user_id,
            "image_count":    len(body.images),
            "trade_category": body.trade_category,
        },
    )

    try:
        result = await photo_analyzer.analyse(
            images=body.images,
            description=body.description,
            trade_category=body.trade_category,
        )
    except ValueError as exc:
        # Bad input — images unreadable, all blurry, etc.
        log.warning(
            "photo_analysis_bad_input",
            extra={"user_id": user_id, "error": str(exc)},
        )
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        msg = str(exc)
        if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower():
            log.error("photo_gemini_quota", extra={"user_id": user_id, "error": msg})
            raise HTTPException(
                status_code=429,
                detail="Gemini API quota exceeded. Check billing at https://aistudio.google.com/",
            )
        log.error("photo_analysis_failed", extra={"user_id": user_id, "error": msg})
        # Do not leak internal error detail to the caller
        raise HTTPException(
            status_code=500,
            detail="Photo analysis failed. Please try again or contact support.",
        )

    # Validate urgency_score is within bounds (Gemini occasionally goes off-range)
    result["urgency_score"] = max(1, min(10, int(result.get("urgency_score", 1))))

    return result
