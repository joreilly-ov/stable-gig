"""Contractor matching router.

GET  /jobs/{job_id}/contractors/matches
    Returns contractors ranked by semantic similarity to the job's RFP (or
    description if no RFP has been generated yet).  Only the job owner can call
    this — contractors cannot discover who else is being considered.

POST /me/contractor/embed-profile
    Contractor regenerates their own profile embedding.  Should be called
    whenever they update their business details (business name, trade
    activities, years of experience, etc.).

Auth: both endpoints require a valid Supabase JWT.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.database import get_supabase_admin
from app.dependencies import get_current_user
from app.services import contractor_matcher

router = APIRouter(tags=["contractor-matching"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_admin()


def _get_job_or_404(job_id: str) -> dict:
    res = _db().table("jobs").select("*").eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    return res.data[0]


def _get_contractor_id_or_403(user_id: str) -> str:
    """Return the contractor.id for this user, or raise 403."""
    res = (
        _db()
        .table("contractors")
        .select("id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=403, detail="Only registered contractors can call this endpoint")
    return res.data[0]["id"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/contractors/matches")
async def match_contractors_for_job(
    job_id: str,
    limit: int = 10,
    user=Depends(get_current_user),
):
    """Return contractors best matched to this job.

    Ranking uses cosine similarity between the job's RFP text and each
    contractor's stored profile embedding.  If no embeddings exist yet (new
    platform with no contractors who have called ``/me/contractor/embed-profile``),
    the endpoint falls back to a simple activity-category filter.

    Response fields per contractor:
    - All ``contractors`` table columns
    - ``contractor_details``: nested details object (may be null)
    - ``match_score``: float 0–1 from embedding similarity, or null for fallback rows

    Query params:
    - ``limit`` (int, default 10): max contractors to return
    """
    user_id = str(user.id)
    job = _get_job_or_404(job_id)

    if job["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Only the job owner can view contractor matches")

    if limit < 1 or limit > 50:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 50")

    try:
        matches = await contractor_matcher.find_matching_contractors(job=job, limit=limit)
    except Exception as exc:
        log.error("contractor_match_failed", extra={"job_id": job_id, "error": str(exc)})
        raise HTTPException(status_code=503, detail="Matching service temporarily unavailable")

    return {
        "job_id":   job_id,
        "matches":  matches,
        "strategy": "embedding" if (matches and matches[0].get("match_score") is not None) else "activity_fallback",
    }


@router.post("/me/contractor/embed-profile", status_code=200)
async def embed_my_profile(user=Depends(get_current_user)):
    """Regenerate the embedding for the calling contractor's profile.

    This should be called after any change to business details — the embedding
    powers the semantic matching shown to homeowners when they request bids.

    Response:
    - ``profile_text``: the plain-text summary that was embedded
    - ``embedding_dimensions``: 768 (confirms model version)
    """
    user_id = str(user.id)
    contractor_id = _get_contractor_id_or_403(user_id)

    try:
        result = await contractor_matcher.update_contractor_embedding(contractor_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Profile has insufficient data to embed: {exc}",
        )
    except Exception as exc:
        log.error(
            "embed_profile_failed",
            extra={"contractor_id": contractor_id, "error": str(exc)},
        )
        raise HTTPException(status_code=503, detail="Embedding service temporarily unavailable")

    log.info("embed_profile_complete", extra={"contractor_id": contractor_id})
    return result
