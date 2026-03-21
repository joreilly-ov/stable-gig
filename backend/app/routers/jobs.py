"""Jobs resource — homeowner job management.

POST /jobs             — homeowner creates a job (starts in 'draft')
GET  /jobs             — list jobs:
                           • contractors → all 'open' jobs
                           • homeowners  → their own jobs (all statuses)
GET  /jobs/{id}        — detail (owner or contractor on an open job)
PATCH /jobs/{id}       — homeowner updates job fields or status

Status lifecycle (homeowner controls):
  draft → open → awarded → in_progress → completed | cancelled

Auth: all endpoints require a valid Supabase JWT.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import get_supabase_admin
from app.dependencies import get_current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])
log = logging.getLogger(__name__)

_VALID_ACTIVITIES = {
    "plumbing", "electrical", "structural", "damp", "roofing",
    "carpentry", "painting", "tiling", "flooring", "heating_hvac",
    "glazing", "landscaping", "general",
}

_VALID_STATUSES = {"draft", "open", "awarded", "in_progress", "completed", "cancelled"}

# Transitions a homeowner is allowed to make (status → set of reachable statuses)
_OWNER_TRANSITIONS: dict[str, set[str]] = {
    "draft":       {"open", "cancelled"},
    "open":        {"cancelled"},
    "awarded":     {"in_progress", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed":   set(),
    "cancelled":   set(),
}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class JobCreate(BaseModel):
    title:       str  = Field(..., min_length=3, max_length=200)
    description: str  = Field(..., min_length=10, max_length=5_000)
    activity:    str  = Field(..., description=f"One of: {sorted(_VALID_ACTIVITIES)}")
    postcode:    str  = Field(..., min_length=2, max_length=10)
    analysis_result: dict | None = Field(
        default=None,
        description="Gemini analysis JSON from POST /analyse or /analyse/photos — stored verbatim.",
    )

    def validate_activity(self) -> None:
        if self.activity not in _VALID_ACTIVITIES:
            raise ValueError(f"activity must be one of {sorted(_VALID_ACTIVITIES)}")


class JobPatch(BaseModel):
    title:       str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, min_length=10, max_length=5_000)
    postcode:    str | None = Field(default=None, min_length=2, max_length=10)
    status:      str | None = Field(default=None, description="New status to transition to.")


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


def _is_contractor(user_id: str) -> bool:
    res = _db().table("contractors").select("id").eq("user_id", user_id).limit(1).execute()
    return bool(res.data)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_job(body: JobCreate, user=Depends(get_current_user)):
    """Create a new job in 'draft' status."""
    if body.activity not in _VALID_ACTIVITIES:
        raise HTTPException(
            status_code=422,
            detail=f"activity must be one of {sorted(_VALID_ACTIVITIES)}",
        )

    user_id = str(user.id)
    payload = {
        "user_id":         user_id,
        "title":           body.title,
        "description":     body.description,
        "activity":        body.activity,
        "postcode":        body.postcode.upper().strip(),
        "status":          "draft",
    }
    if body.analysis_result is not None:
        payload["analysis_result"] = body.analysis_result

    res = _db().table("jobs").insert(payload).execute()
    if not res.data:
        log.error("job_create_failed", extra={"user_id": user_id})
        raise HTTPException(status_code=500, detail="Failed to create job")

    log.info("job_created", extra={"user_id": user_id, "job_id": res.data[0]["id"]})
    return res.data[0]


@router.get("")
async def list_jobs(user=Depends(get_current_user)):
    """
    List jobs.
    - Contractors see all 'open' jobs.
    - Homeowners see their own jobs (all statuses).
    """
    user_id = str(user.id)

    if _is_contractor(user_id):
        res = _db().table("jobs").select("*").eq("status", "open").order("created_at", desc=True).execute()
    else:
        res = _db().table("jobs").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()

    return res.data


@router.get("/{job_id}")
async def get_job(job_id: str, user=Depends(get_current_user)):
    """Get job detail. Owner sees any status; contractors only see open jobs."""
    user_id = str(user.id)
    job = _get_job_or_404(job_id)

    is_owner = job["user_id"] == user_id
    is_open  = job["status"] == "open"

    if not is_owner and not (is_open and _is_contractor(user_id)):
        raise HTTPException(status_code=403, detail="Not authorised to view this job")

    return job


@router.patch("/{job_id}")
async def update_job(job_id: str, body: JobPatch, user=Depends(get_current_user)):
    """
    Update a job's fields or status.  Only the owner can call this.

    Status transitions allowed by the owner:
      draft → open | cancelled
      open  → cancelled
      awarded → in_progress | cancelled
      in_progress → completed | cancelled
    """
    user_id = str(user.id)
    job = _get_job_or_404(job_id)

    if job["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorised to update this job")

    updates: dict = {}

    if body.title is not None:
        updates["title"] = body.title
    if body.description is not None:
        updates["description"] = body.description
    if body.postcode is not None:
        updates["postcode"] = body.postcode.upper().strip()

    if body.status is not None:
        current_status = job["status"]
        allowed = _OWNER_TRANSITIONS.get(current_status, set())
        if body.status not in allowed:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cannot transition from '{current_status}' to '{body.status}'. "
                    f"Allowed transitions: {sorted(allowed) or 'none'}"
                ),
            )
        updates["status"] = body.status

    if not updates:
        return job  # nothing to do

    res = _db().table("jobs").update(updates).eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to update job")

    log.info("job_updated", extra={"user_id": user_id, "job_id": job_id, "updates": list(updates)})
    return res.data[0]
