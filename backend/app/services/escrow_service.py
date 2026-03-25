"""Escrow orchestration service.

Manages the full payment lifecycle for a job:

  initiate()       — homeowner triggers payment; creates Stripe PaymentIntent;
                     returns client_secret for the Lovable frontend to mount
                     the Stripe Payment Element
  confirm_held()   — called by the Stripe webhook when payment succeeds;
                     updates transaction + job escrow_status to 'held'
  release()        — homeowner approves work; funds transferred to contractor
  refund()         — homeowner or admin requests refund; funds returned

State machine (escrow_transactions.status):
  pending → processing → held → released
                              ↘ refunded
  pending → failed

jobs.escrow_status mirrors the transaction status for fast reads:
  pending → held → funds_released | refunded

All DB writes use the service-role client (bypasses RLS); ownership is
validated in Python before any mutation.
"""

import logging
from datetime import datetime, timezone

from app.database import get_supabase_admin
from app.services.payment_provider import get_escrow_provider

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_job(db, job_id: str) -> dict:
    res = db.table("jobs").select("*").eq("id", job_id).execute()
    if not res.data:
        raise LookupError(f"Job {job_id!r} not found")
    return res.data[0]


def _get_accepted_bid(db, job_id: str) -> dict:
    res = (
        db.table("bids")
        .select("*")
        .eq("job_id", job_id)
        .eq("status", "accepted")
        .limit(1)
        .execute()
    )
    if not res.data:
        raise LookupError("No accepted bid found for this job")
    return res.data[0]


def _get_transaction(db, job_id: str) -> dict | None:
    res = db.table("escrow_transactions").select("*").eq("job_id", job_id).execute()
    return res.data[0] if res.data else None


def _get_contractor_stripe_account(db, contractor_id: str) -> str | None:
    res = (
        db.table("contractor_details")
        .select("stripe_account_id")
        .eq("id", contractor_id)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0].get("stripe_account_id") or None
    return None


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

async def initiate(job_id: str, user_id: str) -> dict:
    """Create a PaymentIntent for the accepted bid amount.

    Returns:
        {client_secret, provider_ref, amount_pence, currency, stripe_publishable_key}

    Raises:
        LookupError:    job or accepted bid not found
        PermissionError: caller is not the job owner
        ValueError:     job not in 'awarded' status, or escrow already active
        RuntimeError:   payment provider not configured
    """
    db = get_supabase_admin()

    job = _get_job(db, job_id)
    if job["user_id"] != user_id:
        raise PermissionError("Not the job owner")
    if job["status"] != "awarded":
        raise ValueError(
            f"Payment can only be initiated on an 'awarded' job (current status: '{job['status']}')"
        )

    # Idempotency: if we already have a live transaction, return it
    existing_tx = _get_transaction(db, job_id)
    if existing_tx and existing_tx["status"] in ("processing", "held", "released"):
        raise ValueError(
            f"Escrow already in status '{existing_tx['status']}' — cannot re-initiate"
        )

    bid = _get_accepted_bid(db, job_id)

    provider = get_escrow_provider()
    intent = await provider.create_payment_intent(
        amount_pence=bid["amount_pence"],
        currency="gbp",
        metadata={
            "job_id":         job_id,
            "bid_id":         str(bid["id"]),
            "homeowner_id":   user_id,
            "contractor_id":  str(bid["contractor_id"]),
        },
    )

    tx_payload = {
        "job_id":         job_id,
        "bid_id":         str(bid["id"]),
        "homeowner_id":   user_id,
        "contractor_id":  str(bid["contractor_id"]),
        "amount_pence":   bid["amount_pence"],
        "currency":       "gbp",
        "provider":       "stripe",
        "provider_ref":   intent.provider_ref,
        "status":         "processing",
    }
    if existing_tx:
        db.table("escrow_transactions").update(tx_payload).eq("job_id", job_id).execute()
    else:
        db.table("escrow_transactions").insert(tx_payload).execute()

    log.info("escrow_initiated", extra={"job_id": job_id, "amount_pence": bid["amount_pence"]})

    from app.config import settings  # noqa: PLC0415 — avoid circular at module level
    return {
        "client_secret":          intent.client_secret,
        "provider_ref":           intent.provider_ref,
        "amount_pence":           bid["amount_pence"],
        "currency":               "gbp",
        "stripe_publishable_key": settings.stripe_publishable_key,
    }


async def confirm_held(job_id: str, provider_ref: str) -> None:
    """Mark escrow as held after the provider confirms payment succeeded.

    Called by the Stripe webhook handler on payment_intent.succeeded.
    Also called by the manual-confirm endpoint (useful for testing).
    """
    db = get_supabase_admin()

    # Allow lookup by job_id or provider_ref
    if job_id:
        tx = _get_transaction(db, job_id)
    else:
        res = (
            db.table("escrow_transactions")
            .select("*")
            .eq("provider_ref", provider_ref)
            .limit(1)
            .execute()
        )
        tx = res.data[0] if res.data else None

    if not tx:
        log.warning("escrow_confirm_no_tx", extra={"job_id": job_id, "provider_ref": provider_ref})
        return

    if tx["status"] == "held":
        return  # already confirmed — idempotent

    db.table("escrow_transactions").update({
        "status":   "held",
        "held_at":  _now_iso(),
    }).eq("id", tx["id"]).execute()

    db.table("jobs").update({
        "escrow_status": "held",
    }).eq("id", tx["job_id"]).execute()

    log.info("escrow_held", extra={"job_id": tx["job_id"], "provider_ref": provider_ref})


async def release(job_id: str, user_id: str, note: str = "") -> dict:
    """Release held funds to the contractor.

    If the contractor has a stripe_account_id, funds are transferred immediately.
    If not, the release is recorded and the payout is flagged as pending manual
    processing — the platform admin completes it via the Stripe Dashboard.

    Returns:
        {status, transfer_id | None, payout_pending}
    """
    db = get_supabase_admin()

    job = _get_job(db, job_id)
    if job["user_id"] != user_id:
        raise PermissionError("Not the job owner")
    if job.get("escrow_status") != "held":
        raise ValueError(
            f"Funds are not held — escrow status is '{job.get('escrow_status')}'"
        )

    tx = _get_transaction(db, job_id)
    if not tx or tx["status"] != "held":
        raise ValueError("No held escrow transaction found for this job")

    stripe_account_id = _get_contractor_stripe_account(db, tx["contractor_id"])
    transfer_id = None
    payout_pending = False

    if stripe_account_id:
        provider = get_escrow_provider()
        result = await provider.transfer_to_contractor(
            amount_pence=tx["amount_pence"],
            currency=tx["currency"],
            contractor_account_id=stripe_account_id,
            payment_intent_id=tx["provider_ref"],
        )
        transfer_id = result.transfer_id
    else:
        payout_pending = True
        log.warning(
            "escrow_release_no_stripe_account",
            extra={"job_id": job_id, "contractor_id": tx["contractor_id"]},
        )

    db.table("escrow_transactions").update({
        "status":                 "released",
        "released_at":            _now_iso(),
        "release_note":           note or None,
        "provider_transfer_ref":  transfer_id,
    }).eq("id", tx["id"]).execute()

    db.table("jobs").update({"escrow_status": "funds_released"}).eq("id", job_id).execute()

    log.info("escrow_released", extra={"job_id": job_id, "transfer_id": transfer_id})

    return {
        "status":         "released",
        "transfer_id":    transfer_id,
        "payout_pending": payout_pending,
    }


async def refund(job_id: str, user_id: str, reason: str = "") -> dict:
    """Refund held funds to the homeowner.

    Returns:
        {status, refund_id}
    """
    db = get_supabase_admin()

    job = _get_job(db, job_id)
    if job["user_id"] != user_id:
        raise PermissionError("Not the job owner")
    if job.get("escrow_status") != "held":
        raise ValueError(
            f"Cannot refund — escrow status is '{job.get('escrow_status')}'"
        )

    tx = _get_transaction(db, job_id)
    if not tx or tx["status"] != "held":
        raise ValueError("No held escrow transaction found for this job")

    provider = get_escrow_provider()
    result = await provider.refund_payment(
        payment_intent_id=tx["provider_ref"],
        reason=reason or "requested_by_customer",
    )

    db.table("escrow_transactions").update({
        "status":               "refunded",
        "refunded_at":          _now_iso(),
        "provider_refund_ref":  result.refund_id,
        "failure_reason":       reason or None,
    }).eq("id", tx["id"]).execute()

    db.table("jobs").update({"escrow_status": "refunded"}).eq("id", job_id).execute()

    log.info("escrow_refunded", extra={"job_id": job_id, "refund_id": result.refund_id})

    return {"status": "refunded", "refund_id": result.refund_id}


async def get_status(job_id: str, user_id: str) -> dict:
    """Return the current escrow state for a job.

    Raises:
        LookupError:    job not found
        PermissionError: caller is neither the homeowner nor the contractor
    """
    db = get_supabase_admin()

    job = _get_job(db, job_id)

    # Allow both homeowner and contractor to check status
    is_owner = job["user_id"] == user_id
    if not is_owner:
        # Check if caller is the contractor on this job
        tx = _get_transaction(db, job_id)
        if not tx or str(tx.get("homeowner_id")) == user_id:
            pass  # will fail below if not contractor either
        contractor_res = (
            db.table("contractors")
            .select("id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        contractor_id = contractor_res.data[0]["id"] if contractor_res.data else None
        if not contractor_id or (tx and str(tx.get("contractor_id")) != str(contractor_id)):
            raise PermissionError("Not authorised to view escrow for this job")

    tx = _get_transaction(db, job_id)

    return {
        "job_id":                   job_id,
        "job_escrow_status":        job.get("escrow_status"),
        "transaction":              tx,
    }
