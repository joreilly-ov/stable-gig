"""Escrow payment router.

GET  /escrow/config
    Returns the Stripe publishable key for the frontend to initialise the
    Stripe.js SDK.  No auth required — the publishable key is not secret.

POST /jobs/{job_id}/escrow/initiate
    Homeowner creates a PaymentIntent for the accepted bid amount.
    Returns a client_secret that the Lovable frontend passes to the
    Stripe Payment Element to collect card details.

GET  /jobs/{job_id}/escrow
    Homeowner or the job's contractor checks the current escrow status.

POST /jobs/{job_id}/escrow/release
    Homeowner approves completed work; funds are transferred to the contractor.
    If the contractor has not linked a Stripe Connect account, the payout is
    flagged as pending manual processing.

POST /jobs/{job_id}/escrow/refund
    Homeowner requests a refund (dispute or cancellation).
    Only valid while funds are in 'held' status.

POST /webhooks/stripe
    Stripe webhook endpoint — verifies the Stripe-Signature header and
    processes payment_intent.succeeded / payment_intent.payment_failed events.
    Must receive the raw request body (not JSON-parsed) for signature
    verification, so it uses Request directly.

Auth: all /jobs/* endpoints require a valid Supabase JWT.
      /escrow/config and /webhooks/stripe are unauthenticated.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.database import get_supabase_admin
from app.dependencies import get_current_user
from app.services import escrow_service
from app.services.payment_provider import get_escrow_provider

router = APIRouter(tags=["escrow"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ReleaseRequest(BaseModel):
    note: str = Field(
        default="",
        max_length=500,
        description="Optional note from the homeowner approving the work.",
    )


class RefundRequest(BaseModel):
    reason: str = Field(
        default="",
        max_length=500,
        description="Reason for the refund (dispute, cancellation, etc.).",
    )


# ---------------------------------------------------------------------------
# Helpers — map service exceptions to HTTP responses
# ---------------------------------------------------------------------------

def _handle_service_error(exc: Exception, context: str) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, RuntimeError):
        # Provider not configured
        return HTTPException(status_code=503, detail=str(exc))
    log.error(context, extra={"error": str(exc)})
    return HTTPException(status_code=503, detail=f"{context}: service temporarily unavailable")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/escrow/config", include_in_schema=True)
async def escrow_config():
    """Return the Stripe publishable key for frontend SDK initialisation.

    The frontend should call this once on mount to get the key it needs to
    load Stripe.js and render the Payment Element.
    """
    if not settings.stripe_publishable_key:
        raise HTTPException(status_code=503, detail="Payment provider not configured")
    return {"stripe_publishable_key": settings.stripe_publishable_key}


@router.post("/jobs/{job_id}/escrow/initiate", status_code=200)
async def initiate_escrow(job_id: str, user=Depends(get_current_user)):
    """Initiate escrow payment for an awarded job.

    The job must be in 'awarded' status (a bid has been accepted).
    Returns a Stripe client_secret — pass this to the Stripe Payment Element
    on the frontend to collect and confirm the payment.

    Response:
    - client_secret:          for Stripe.js confirmPayment()
    - provider_ref:           PaymentIntent ID (store for your records)
    - amount_pence:           amount charged (from the accepted bid)
    - currency:               always "gbp"
    - stripe_publishable_key: convenience — same as GET /escrow/config
    """
    try:
        result = await escrow_service.initiate(job_id=job_id, user_id=str(user.id))
    except Exception as exc:
        raise _handle_service_error(exc, "escrow_initiate")
    return result


@router.get("/jobs/{job_id}/escrow")
async def get_escrow_status(job_id: str, user=Depends(get_current_user)):
    """Return current escrow status for a job.

    Accessible to both the homeowner and the job's contractor.

    Response:
    - job_escrow_status:  current status on the jobs table
    - transaction:        full escrow_transactions record (or null if not yet initiated)
    """
    try:
        result = await escrow_service.get_status(job_id=job_id, user_id=str(user.id))
    except Exception as exc:
        raise _handle_service_error(exc, "escrow_status")
    return result


@router.post("/jobs/{job_id}/escrow/release", status_code=200)
async def release_escrow(
    job_id: str,
    body: ReleaseRequest,
    user=Depends(get_current_user),
):
    """Release held funds to the contractor.

    Can only be called by the homeowner when escrow_status is 'held'.
    If the contractor has not linked a Stripe Connect account, payout_pending
    will be true — the platform admin must complete the transfer manually.

    Response:
    - status:         "released"
    - transfer_id:    Stripe Transfer ID (null if payout_pending)
    - payout_pending: true if contractor has no connected Stripe account
    """
    try:
        result = await escrow_service.release(
            job_id=job_id,
            user_id=str(user.id),
            note=body.note,
        )
    except Exception as exc:
        raise _handle_service_error(exc, "escrow_release")
    return result


@router.post("/jobs/{job_id}/escrow/refund", status_code=200)
async def refund_escrow(
    job_id: str,
    body: RefundRequest,
    user=Depends(get_current_user),
):
    """Refund held funds to the homeowner.

    Only valid while escrow_status is 'held'.  Once released, refunds must be
    handled manually through the payment provider dashboard.

    Response:
    - status:    "refunded"
    - refund_id: Stripe Refund ID
    """
    try:
        result = await escrow_service.refund(
            job_id=job_id,
            user_id=str(user.id),
            reason=body.reason,
        )
    except Exception as exc:
        raise _handle_service_error(exc, "escrow_refund")
    return result


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------

@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(request: Request):
    """Receive and process Stripe webhook events.

    Stripe sends the raw JSON body with a Stripe-Signature header.  We MUST
    read the raw bytes here — any middleware that re-parses the body would
    invalidate the HMAC signature.

    Handled events:
      payment_intent.succeeded      → escrow marked as 'held'
      payment_intent.payment_failed → escrow marked as 'failed' with reason
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # Verify signature
    try:
        provider = get_escrow_provider()
        event = provider.verify_webhook(payload, sig_header)
    except (RuntimeError, ValueError) as exc:
        log.warning("stripe_webhook_rejected", extra={"reason": str(exc)})
        raise HTTPException(status_code=400, detail="Invalid webhook payload or signature")

    event_type = event.get("type", "")
    pi = event.get("data", {}).get("object", {})
    metadata = pi.get("metadata", {})
    job_id = metadata.get("job_id")

    if not job_id:
        # Not a job-related payment — acknowledge and ignore
        return {"ok": True}

    if event_type == "payment_intent.succeeded":
        try:
            await escrow_service.confirm_held(
                job_id=job_id,
                provider_ref=pi.get("id", ""),
            )
        except Exception as exc:
            log.error("webhook_confirm_held_failed", extra={"job_id": job_id, "error": str(exc)})
            # Return 200 to Stripe so it doesn't retry; log internally
        log.info("webhook_payment_succeeded", extra={"job_id": job_id})

    elif event_type == "payment_intent.payment_failed":
        reason = pi.get("last_payment_error", {}).get("message", "payment failed")
        db = get_supabase_admin()
        db.table("escrow_transactions").update({
            "status":         "failed",
            "failure_reason": reason,
        }).eq("job_id", job_id).execute()
        log.warning("webhook_payment_failed", extra={"job_id": job_id, "reason": reason})

    return {"ok": True}
