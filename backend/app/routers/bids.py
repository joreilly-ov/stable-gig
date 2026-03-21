"""Bids resource — contractor bidding on open jobs.

POST  /jobs/{job_id}/bids              — contractor submits a bid
GET   /jobs/{job_id}/bids              — homeowner sees all bids; contractor sees own
PATCH /jobs/{job_id}/bids/{bid_id}     — homeowner accepts or rejects a bid
GET   /me/bids                         — contractor sees all their bids (any job)

Business rules:
  • Only contractors may place bids.
  • A bid can only be placed on an 'open' job.
  • One bid per contractor per job (enforced by DB UNIQUE constraint).
  • Accepting a bid:
      - Sets that bid to 'accepted'
      - Sets all other bids on the same job to 'rejected'
      - Moves the job to 'awarded'
  • Only the job owner can accept/reject bids.

Auth: all endpoints require a valid Supabase JWT.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import get_supabase_admin
from app.dependencies import get_current_user

router = APIRouter(tags=["bids"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class BidCreate(BaseModel):
    amount_pence: int = Field(..., gt=0, description="Quote price in pence (e.g. 15000 = £150.00)")
    note:         str = Field(
        ...,
        min_length=10,
        max_length=2_000,
        description="Scope of work — what the contractor plans to do.",
    )


class BidAction(BaseModel):
    action: str = Field(..., description="'accept' or 'reject'")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_admin()


def _get_contractor_or_403(user_id: str) -> dict:
    """Return contractor row for the user or raise 403."""
    res = _db().table("contractors").select("id").eq("user_id", user_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=403, detail="Only registered contractors may place bids")
    return res.data[0]


def _get_job_or_404(job_id: str) -> dict:
    res = _db().table("jobs").select("*").eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    return res.data[0]


def _get_bid_or_404(bid_id: str, job_id: str) -> dict:
    res = (
        _db()
        .table("bids")
        .select("*")
        .eq("id", bid_id)
        .eq("job_id", job_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Bid not found")
    return res.data[0]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/jobs/{job_id}/bids", status_code=201)
async def place_bid(job_id: str, body: BidCreate, user=Depends(get_current_user)):
    """Contractor places a bid on an open job."""
    user_id    = str(user.id)
    contractor = _get_contractor_or_403(user_id)
    job        = _get_job_or_404(job_id)

    if job["status"] != "open":
        raise HTTPException(
            status_code=422,
            detail=f"Bids can only be placed on 'open' jobs (current status: '{job['status']}')",
        )

    # Check for existing bid from this contractor
    existing = (
        _db()
        .table("bids")
        .select("id")
        .eq("job_id", job_id)
        .eq("contractor_id", contractor["id"])
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status_code=409,
            detail="You have already placed a bid on this job. Update your existing bid instead.",
        )

    res = _db().table("bids").insert({
        "job_id":        job_id,
        "contractor_id": contractor["id"],
        "amount_pence":  body.amount_pence,
        "note":          body.note,
        "status":        "pending",
    }).execute()

    if not res.data:
        log.error("bid_create_failed", extra={"user_id": user_id, "job_id": job_id})
        raise HTTPException(status_code=500, detail="Failed to place bid")

    bid = res.data[0]
    log.info("bid_placed", extra={"user_id": user_id, "job_id": job_id, "bid_id": bid["id"]})
    return bid


@router.get("/jobs/{job_id}/bids")
async def list_bids(job_id: str, user=Depends(get_current_user)):
    """
    List bids on a job.
    - Job owner: sees all bids with contractor info.
    - Contractor: sees only their own bid.
    """
    user_id = str(user.id)
    job     = _get_job_or_404(job_id)

    if job["user_id"] == user_id:
        # Owner: return all bids enriched with contractor info
        res = (
            _db()
            .table("bids")
            .select("*, contractors(id, business_name, postcode, activities)")
            .eq("job_id", job_id)
            .order("created_at")
            .execute()
        )
        return res.data

    # Contractor: only their own bid
    contractor_res = _db().table("contractors").select("id").eq("user_id", user_id).limit(1).execute()
    if not contractor_res.data:
        raise HTTPException(status_code=403, detail="Not authorised to view bids on this job")

    contractor_id = contractor_res.data[0]["id"]
    res = (
        _db()
        .table("bids")
        .select("*")
        .eq("job_id", job_id)
        .eq("contractor_id", contractor_id)
        .execute()
    )
    return res.data


@router.patch("/jobs/{job_id}/bids/{bid_id}")
async def action_bid(
    job_id: str,
    bid_id: str,
    body: BidAction,
    user=Depends(get_current_user),
):
    """
    Homeowner accepts or rejects a specific bid.

    Accepting a bid:
      - Sets this bid to 'accepted'
      - Sets all other bids on the job to 'rejected'
      - Moves the job to 'awarded'
    """
    if body.action not in {"accept", "reject"}:
        raise HTTPException(status_code=422, detail="action must be 'accept' or 'reject'")

    user_id = str(user.id)
    job     = _get_job_or_404(job_id)

    if job["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Only the job owner can accept or reject bids")

    if job["status"] != "open":
        raise HTTPException(
            status_code=422,
            detail=f"Bids can only be actioned on 'open' jobs (current status: '{job['status']}')",
        )

    bid = _get_bid_or_404(bid_id, job_id)

    if bid["status"] != "pending":
        raise HTTPException(
            status_code=422,
            detail=f"Bid is already '{bid['status']}' and cannot be changed",
        )

    if body.action == "reject":
        res = _db().table("bids").update({"status": "rejected"}).eq("id", bid_id).execute()
        log.info("bid_rejected", extra={"user_id": user_id, "bid_id": bid_id})
        return res.data[0] if res.data else bid

    # Accept: update this bid, reject all others, award the job
    _db().table("bids").update({"status": "accepted"}).eq("id", bid_id).execute()

    # Reject all remaining pending bids on this job
    _db().table("bids").update({"status": "rejected"}).eq("job_id", job_id).neq("id", bid_id).execute()

    # Move the job to 'awarded'
    job_res = _db().table("jobs").update({"status": "awarded"}).eq("id", job_id).execute()

    log.info(
        "bid_accepted",
        extra={"user_id": user_id, "job_id": job_id, "bid_id": bid_id},
    )

    # Return the updated bid
    res = _db().table("bids").select("*").eq("id", bid_id).execute()
    return {
        "bid": res.data[0] if res.data else None,
        "job": job_res.data[0] if job_res.data else None,
    }


@router.get("/me/bids")
async def my_bids(user=Depends(get_current_user)):
    """Contractor sees all their bids across all jobs, newest first."""
    user_id = str(user.id)
    contractor_res = _db().table("contractors").select("id").eq("user_id", user_id).limit(1).execute()
    if not contractor_res.data:
        raise HTTPException(status_code=403, detail="Only registered contractors can view their bids")

    contractor_id = contractor_res.data[0]["id"]
    res = (
        _db()
        .table("bids")
        .select("*, jobs(id, title, description, activity, postcode, status, created_at)")
        .eq("contractor_id", contractor_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data
