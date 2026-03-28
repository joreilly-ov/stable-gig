"""Job milestones and photo evidence.

POST  /jobs/{job_id}/milestones                             — homeowner creates milestones
GET   /jobs/{job_id}/milestones                             — list milestones with photos
POST  /jobs/{job_id}/milestones/{milestone_id}/photos       — contractor submits photo evidence
PATCH /jobs/{job_id}/milestones/{milestone_id}              — homeowner approves or rejects

Milestone status lifecycle:
  pending → submitted  (contractor uploads ≥1 photo)
  submitted → approved (homeowner approves)  ← terminal
  submitted → rejected (homeowner rejects; contractor can re-submit)
  rejected → submitted (contractor re-submits after rejection)

Photo evidence submission optionally re-uses the existing TradePhotoAnalyzer
service to produce an AI verification of the completed work
(pass ?analyse=true on the POST /photos endpoint).

Auth: all endpoints require a valid Supabase JWT.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import get_supabase_admin
from app.dependencies import get_current_user

router = APIRouter(tags=["milestones"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class MilestoneCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    order_index: int = Field(default=0, ge=0)


class MilestoneBatch(BaseModel):
    milestones: list[MilestoneCreate] = Field(
        ..., min_length=1, max_length=20,
        description="Ordered list of milestones to create for the job.",
    )


class PhotoSubmit(BaseModel):
    image_source: str = Field(
        ...,
        description="HTTPS URL or base64 data URI of the completion photo.",
    )
    note: str | None = Field(default=None, max_length=500)


class MilestoneAction(BaseModel):
    action: str = Field(..., description="'approve' or 'reject'")


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


def _get_milestone_or_404(milestone_id: str, job_id: str) -> dict:
    res = (
        _db()
        .table("job_milestones")
        .select("*")
        .eq("id", milestone_id)
        .eq("job_id", job_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return res.data[0]


def _get_contractor_id_or_none(user_id: str) -> str | None:
    # Under Clean Split, contractors.id = auth.users.id — no user_id column.
    res = (
        _db()
        .table("contractors")
        .select("id")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    return res.data[0]["id"] if res.data else None


def _is_accepted_contractor(job_id: str, contractor_id: str) -> bool:
    """Return True if this contractor has the accepted bid on the job."""
    res = (
        _db()
        .table("bids")
        .select("id")
        .eq("job_id", job_id)
        .eq("contractor_id", contractor_id)
        .eq("status", "accepted")
        .limit(1)
        .execute()
    )
    return bool(res.data)


def _enrich_with_photos(milestones: list[dict]) -> list[dict]:
    """Attach photos list to each milestone dict."""
    if not milestones:
        return milestones
    ids = [m["id"] for m in milestones]
    photo_res = (
        _db()
        .table("milestone_photos")
        .select("*")
        .in_("milestone_id", ids)
        .order("created_at")
        .execute()
    )
    photos_by_milestone: dict[str, list] = {m["id"]: [] for m in milestones}
    for p in (photo_res.data or []):
        mid = p.get("milestone_id")
        if mid in photos_by_milestone:
            photos_by_milestone[mid].append(p)
    return [{**m, "photos": photos_by_milestone[m["id"]]} for m in milestones]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/jobs/{job_id}/milestones", status_code=201)
async def create_milestones(
    job_id: str,
    body: MilestoneBatch,
    user=Depends(get_current_user),
):
    """Homeowner defines milestones for a job.

    Can be called on jobs with status 'awarded' or 'in_progress'.
    Existing milestones are not deleted — new ones are appended.
    """
    user_id = str(user.id)
    job     = _get_job_or_404(job_id)

    if job["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Only the job owner can define milestones")

    if job["status"] not in {"awarded", "in_progress"}:
        raise HTTPException(
            status_code=422,
            detail=f"Milestones can only be added to 'awarded' or 'in_progress' jobs (current: '{job['status']}')",
        )

    rows = [
        {
            "job_id":      job_id,
            "title":       m.title,
            "description": m.description,
            "order_index": m.order_index if m.order_index else i,
        }
        for i, m in enumerate(body.milestones)
    ]

    res = _db().table("job_milestones").insert(rows).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to create milestones")

    log.info(
        "milestones_created",
        extra={"user_id": user_id, "job_id": job_id, "count": len(rows)},
    )
    return res.data


@router.get("/jobs/{job_id}/milestones")
async def list_milestones(job_id: str, user=Depends(get_current_user)):
    """List milestones with their photo evidence.

    Accessible to the job owner and the job's accepted contractor.
    """
    user_id = str(user.id)
    job     = _get_job_or_404(job_id)

    is_owner = job["user_id"] == user_id
    if not is_owner:
        contractor_id = _get_contractor_id_or_none(user_id)
        if not contractor_id or not _is_accepted_contractor(job_id, contractor_id):
            raise HTTPException(status_code=403, detail="Not authorised to view milestones for this job")

    res = (
        _db()
        .table("job_milestones")
        .select("*")
        .eq("job_id", job_id)
        .order("order_index")
        .order("created_at")
        .execute()
    )
    return _enrich_with_photos(res.data or [])


@router.post("/jobs/{job_id}/milestones/{milestone_id}/photos", status_code=201)
async def submit_photo(
    job_id:       str,
    milestone_id: str,
    body:         PhotoSubmit,
    user=Depends(get_current_user),
    analyse: bool = Query(
        default=False,
        description="Run AI analysis on the photo and include the result.",
    ),
):
    """Accepted contractor submits photo evidence for a milestone.

    Moves the milestone status from 'pending' or 'rejected' → 'submitted'.
    Pass ?analyse=true to also run the TradePhotoAnalyzer and return an
    AI-generated assessment alongside the saved photo record.
    """
    user_id       = str(user.id)
    contractor_id = _get_contractor_id_or_none(user_id)
    if not contractor_id:
        raise HTTPException(status_code=403, detail="Only registered contractors may submit photos")

    job       = _get_job_or_404(job_id)
    milestone = _get_milestone_or_404(milestone_id, job_id)

    if not _is_accepted_contractor(job_id, contractor_id):
        raise HTTPException(status_code=403, detail="Only the accepted contractor may submit milestone photos")

    if milestone["status"] == "approved":
        raise HTTPException(status_code=409, detail="This milestone is already approved")

    # Save the photo
    photo_res = _db().table("milestone_photos").insert({
        "milestone_id": milestone_id,
        "job_id":       job_id,
        "uploaded_by":  user_id,
        "image_source": body.image_source,
        "note":         body.note,
    }).execute()

    if not photo_res.data:
        raise HTTPException(status_code=500, detail="Failed to save photo")

    # Advance milestone to 'submitted' (unless already there)
    if milestone["status"] in {"pending", "rejected"}:
        _db().table("job_milestones").update({"status": "submitted"}).eq("id", milestone_id).execute()

    photo = photo_res.data[0]
    result: dict = {"photo": photo, "milestone_status": "submitted"}

    # Optional AI analysis of the completion photo
    if analyse:
        try:
            from app.services import photo_analyzer
            ai = await photo_analyzer.analyse(
                images=[body.image_source],
                description=f"Completion photo for milestone: {milestone['title']}",
                trade_category=None,
            )
            result["ai_analysis"] = ai
        except Exception as exc:
            log.warning("milestone_photo_analysis_failed", extra={"milestone_id": milestone_id, "error": str(exc)})
            result["ai_analysis"] = None

    log.info(
        "milestone_photo_submitted",
        extra={"user_id": user_id, "milestone_id": milestone_id, "job_id": job_id},
    )
    return result


@router.patch("/jobs/{job_id}/milestones/{milestone_id}")
async def action_milestone(
    job_id:       str,
    milestone_id: str,
    body:         MilestoneAction,
    user=Depends(get_current_user),
):
    """Job owner approves or rejects a submitted milestone.

    Only milestones in 'submitted' status can be actioned.
    Approval sets status → 'approved' and records approved_at.
    Rejection sets status → 'rejected'; the contractor can re-submit.
    """
    if body.action not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="action must be 'approve' or 'reject'")

    user_id = str(user.id)
    job     = _get_job_or_404(job_id)

    if job["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Only the job owner can approve or reject milestones")

    milestone = _get_milestone_or_404(milestone_id, job_id)

    if milestone["status"] != "submitted":
        raise HTTPException(
            status_code=422,
            detail=f"Only 'submitted' milestones can be actioned (current: '{milestone['status']}')",
        )

    if body.action == "approve":
        updates = {"status": "approved", "approved_at": "now()", "updated_at": "now()"}
    else:
        updates = {"status": "rejected", "updated_at": "now()"}

    res = _db().table("job_milestones").update(updates).eq("id", milestone_id).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to update milestone")

    log.info(
        "milestone_actioned",
        extra={"user_id": user_id, "milestone_id": milestone_id, "action": body.action},
    )
    return res.data[0]
