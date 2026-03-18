"""Contractor review routes — /reviews."""

from fastapi import APIRouter, Depends, HTTPException

from app.database import get_supabase
from app.dependencies import get_current_user
from app.models.schemas import ReviewCreate, ReviewResponse, ReviewSummary

router = APIRouter(prefix="/reviews", tags=["reviews"])

_RATING_KEYS = ("overall", "quality", "timeliness", "communication", "value", "tidiness")


@router.post("", response_model=ReviewResponse, status_code=201)
async def submit_review(body: ReviewCreate, user=Depends(get_current_user)):
    """Submit a review for a contractor on a completed job.

    The caller must be the owner of the referenced job.
    Only one review per job per reviewer is permitted.
    """
    db = get_supabase()
    user_id = str(user.id)

    # Verify the job exists and belongs to this reviewer
    job = (
        db.table("jobs")
        .select("id,user_id")
        .eq("id", body.job_id)
        .maybe_single()
        .execute()
    )
    if not job.data:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.data["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You can only review contractors on your own jobs")

    # Verify the contractor exists
    contractor = (
        db.table("contractors")
        .select("id")
        .eq("id", body.contractor_id)
        .maybe_single()
        .execute()
    )
    if not contractor.data:
        raise HTTPException(status_code=404, detail="Contractor not found")

    # Enforce one review per job per reviewer
    existing = (
        db.table("reviews")
        .select("id")
        .eq("job_id", body.job_id)
        .eq("reviewer_id", user_id)
        .maybe_single()
        .execute()
    )
    if existing.data:
        raise HTTPException(status_code=409, detail="You have already reviewed this job")

    payload = {**body.model_dump(), "reviewer_id": user_id}
    result = db.table("reviews").insert(payload).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save review")

    row = result.data[0]
    profile = (
        db.table("profiles")
        .select("full_name")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    row["reviewer_name"] = profile.data.get("full_name") if profile.data else None
    return row


@router.get("/contractor/{contractor_id}", response_model=list[ReviewResponse])
async def list_contractor_reviews(contractor_id: str):
    """Return all reviews for a contractor, newest first."""
    db = get_supabase()
    result = (
        db.table("reviews")
        .select("*")
        .eq("contractor_id", contractor_id)
        .order("created_at", desc=True)
        .execute()
    )
    reviews = result.data or []

    if reviews:
        reviewer_ids = list({r["reviewer_id"] for r in reviews})
        profiles = (
            db.table("profiles")
            .select("id,full_name")
            .in_("id", reviewer_ids)
            .execute()
        )
        name_map = {p["id"]: p["full_name"] for p in (profiles.data or [])}
        for r in reviews:
            r["reviewer_name"] = name_map.get(r["reviewer_id"])

    return reviews


@router.get("/summary/{contractor_id}", response_model=ReviewSummary)
async def contractor_review_summary(contractor_id: str):
    """Return aggregated rating averages for a contractor."""
    db = get_supabase()
    result = (
        db.table("reviews")
        .select(",".join(_RATING_KEYS))
        .eq("contractor_id", contractor_id)
        .execute()
    )
    rows = result.data or []
    count = len(rows)

    if count == 0:
        return ReviewSummary(
            contractor_id=contractor_id,
            review_count=0,
            avg_overall=0.0,
            avg_quality=0.0,
            avg_timeliness=0.0,
            avg_communication=0.0,
            avg_value=0.0,
            avg_tidiness=0.0,
        )

    def _avg(key: str) -> float:
        return round(sum(r[key] for r in rows) / count, 2)

    return ReviewSummary(
        contractor_id=contractor_id,
        review_count=count,
        avg_overall=_avg("overall"),
        avg_quality=_avg("quality"),
        avg_timeliness=_avg("timeliness"),
        avg_communication=_avg("communication"),
        avg_value=_avg("value"),
        avg_tidiness=_avg("tidiness"),
    )
