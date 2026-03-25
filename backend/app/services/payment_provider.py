"""Payment provider abstraction layer.

Defines the EscrowProvider protocol and concrete implementations.
The rest of the codebase only imports EscrowProvider and get_escrow_provider()
— swapping to a different processor (Plaid, Adyen, etc.) requires only a new
class and a one-line change in get_escrow_provider().

Current implementation: Stripe
  - PaymentIntent with automatic_payment_methods for broad card support
  - Funds captured immediately to the platform's Stripe account
  - Release → Stripe Transfer to the contractor's connected account
  - Refund  → Stripe Refund on the original PaymentIntent

Stripe Connect account setup (out of scope for this module):
  Contractors link their bank via Stripe Connect onboarding; their
  stripe_account_id is stored in contractor_details.stripe_account_id.
  If a contractor has not completed Connect onboarding, release() records the
  intent and flags the payout as pending manual processing.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PaymentIntentResult:
    client_secret: str   # returned to frontend to complete payment
    provider_ref:  str   # PaymentIntent ID — stored for later operations
    status:        str   # e.g. "requires_payment_method"


@dataclass
class TransferResult:
    transfer_id: str
    status:      str = "created"


@dataclass
class RefundResult:
    refund_id: str
    status:    str   # "succeeded" | "pending" | "failed"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class EscrowProvider(ABC):
    """Provider-agnostic escrow interface.

    All async methods should be non-blocking; implementations must dispatch
    any synchronous SDK calls via asyncio.to_thread.
    """

    @abstractmethod
    async def create_payment_intent(
        self,
        amount_pence: int,
        currency: str,
        metadata: dict,
    ) -> PaymentIntentResult:
        """Initialise a payment and return a client_secret for the frontend."""

    @abstractmethod
    async def transfer_to_contractor(
        self,
        amount_pence: int,
        currency: str,
        contractor_account_id: str,
        payment_intent_id: str,
    ) -> TransferResult:
        """Send released funds to the contractor's connected account."""

    @abstractmethod
    async def refund_payment(
        self,
        payment_intent_id: str,
        reason: str = "requested_by_customer",
    ) -> RefundResult:
        """Return funds to the homeowner."""

    @abstractmethod
    def verify_webhook(self, payload: bytes, sig_header: str) -> dict:
        """Verify and parse an inbound provider webhook event.

        Raises ValueError if the signature is invalid.
        """


# ---------------------------------------------------------------------------
# Stripe implementation
# ---------------------------------------------------------------------------

class StripeEscrowProvider(EscrowProvider):
    """Stripe Connect escrow provider.

    Requires:
      STRIPE_SECRET_KEY       — server-side secret key
      STRIPE_WEBHOOK_SECRET   — endpoint signing secret from Stripe Dashboard
    """

    def __init__(self, secret_key: str, webhook_secret: str) -> None:
        import stripe as _stripe  # lazy — not imported until first use
        _stripe.api_key = secret_key
        self._stripe = _stripe
        self._webhook_secret = webhook_secret

    async def create_payment_intent(
        self,
        amount_pence: int,
        currency: str,
        metadata: dict,
    ) -> PaymentIntentResult:
        def _create():
            return self._stripe.PaymentIntent.create(
                amount=amount_pence,
                currency=currency,
                metadata=metadata,
                automatic_payment_methods={"enabled": True},
            )

        pi = await asyncio.to_thread(_create)
        log.info("stripe_payment_intent_created", extra={"pi_id": pi.id, "amount": amount_pence})
        return PaymentIntentResult(
            client_secret=pi.client_secret,
            provider_ref=pi.id,
            status=pi.status,
        )

    async def transfer_to_contractor(
        self,
        amount_pence: int,
        currency: str,
        contractor_account_id: str,
        payment_intent_id: str,
    ) -> TransferResult:
        def _transfer():
            return self._stripe.Transfer.create(
                amount=amount_pence,
                currency=currency,
                destination=contractor_account_id,
                transfer_group=payment_intent_id,
            )

        transfer = await asyncio.to_thread(_transfer)
        log.info(
            "stripe_transfer_created",
            extra={"transfer_id": transfer.id, "destination": contractor_account_id},
        )
        return TransferResult(transfer_id=transfer.id)

    async def refund_payment(
        self,
        payment_intent_id: str,
        reason: str = "requested_by_customer",
    ) -> RefundResult:
        def _refund():
            return self._stripe.Refund.create(
                payment_intent=payment_intent_id,
                reason=reason,
            )

        refund = await asyncio.to_thread(_refund)
        log.info("stripe_refund_created", extra={"refund_id": refund.id, "pi_id": payment_intent_id})
        return RefundResult(refund_id=refund.id, status=refund.status)

    def verify_webhook(self, payload: bytes, sig_header: str) -> dict:
        try:
            event = self._stripe.Webhook.construct_event(
                payload, sig_header, self._webhook_secret
            )
        except self._stripe.error.SignatureVerificationError as exc:
            raise ValueError(f"Invalid webhook signature: {exc}") from exc
        return dict(event)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_escrow_provider() -> EscrowProvider:
    """Return the configured escrow provider.

    Raises RuntimeError if the required keys are not set — the router converts
    this to a 503 so the frontend can show a meaningful error.
    """
    if not settings.stripe_secret_key:
        raise RuntimeError(
            "Payment provider not configured. "
            "Set STRIPE_SECRET_KEY (and STRIPE_WEBHOOK_SECRET) in environment."
        )
    return StripeEscrowProvider(
        secret_key=settings.stripe_secret_key,
        webhook_secret=settings.stripe_webhook_secret,
    )
