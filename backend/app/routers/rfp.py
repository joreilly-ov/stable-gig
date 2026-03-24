"""RFP generation router.

POST /jobs/{job_id}/rfp
    Generates a structured Request for Proposal from the job's stored Gemini
    analysis result, optionally incorporating homeowner clarification answers.

    The generated RFP document and cost-estimate fields are written back to
    the job row so they persist across page refreshes.

Auth: job owner only.

The analysis_result must already be stored on the job (populated when the
homeowner created the job from a video/photo analysis).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import get_supabase_admin
from app.dependencies import get_current_user
from app.services import rfp_generator

router = APIRouter(tags=["rfp"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RFPRequest(BaseModel):
    clarification_answers: dict[str, str] | None = Field(
        default=None,
        description=(
            "Answers to the clarifying_questions returned by the analysis endpoint. "
            "Keys are the questions (verbatim), values are the homeowner's answers."
        ),
    )


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/jobs/{job_id}/rfp", status_code=200)
async def generate_rfp(
    job_id: str,
    body: RFPRequest,
    user=Depends(get_current_user),
):
    """Generate (or regenerate) the RFP for a job.

    Requires ``analysis_result`` to be stored on the job.  Call this after the
    homeowner has answered any clarifying questions from the initial analysis.

    The response includes:
    - The full RFP document (scope, trade category, permit flags, etc.)
    - A private cost estimate range in GBP pence
    - Permit / planning considerations

    The RFP and cost fields are also written back to the job row.
    """
    user_id = str(user.id)
    job = _get_job_or_404(job_id)

    if job["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorised to generate RFP for this job")

    analysis_result = job.get("analysis_result")
    if not analysis_result:
        raise HTTPException(
            status_code=422,
            detail=(
                "No analysis result found on this job. "
                "Submit the job via POST /analyse or /analyse/photos first, "
                "then create the job with analysis_result included."
            ),
        )

    try:
        rfp_doc = await rfp_generator.generate(
            analysis_result=analysis_result,
            clarification_answers=body.clarification_answers,
            postcode=job.get("postcode", ""),
        )
    except ValueError as exc:
        log.warning("rfp_generation_parse_error", extra={"job_id": job_id, "error": str(exc)})
        raise HTTPException(status_code=502, detail=f"AI returned an unexpected response: {exc}")
    except Exception as exc:
        log.error("rfp_generation_failed", extra={"job_id": job_id, "error": str(exc)})
        raise HTTPException(status_code=503, detail="RFP generation service temporarily unavailable")

    cost = rfp_doc.get("cost_estimate", {})

    updates = {
        "rfp_document":            rfp_doc,
        "cost_estimate_low_pence":  cost.get("low_pence"),
        "cost_estimate_high_pence": cost.get("high_pence"),
        "permit_required":          rfp_doc.get("permit_required"),
        "permit_notes":             rfp_doc.get("permit_notes") or None,
    }
    _db().table("jobs").update(updates).eq("id", job_id).execute()

    log.info("rfp_stored", extra={"job_id": job_id, "user_id": user_id})

    return {
        "job_id":       job_id,
        "rfp_document": rfp_doc,
        "cost_estimate": {
            "low_pence":  cost.get("low_pence"),
            "high_pence": cost.get("high_pence"),
            "currency":   "GBP",
            "basis":      cost.get("basis", ""),
        },
        "permit_required": rfp_doc.get("permit_required"),
        "permit_notes":    rfp_doc.get("permit_notes") or "",
    }
