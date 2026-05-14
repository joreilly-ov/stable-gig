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


@dataclass
class ConnectAccountResult:
    account_id: str   # e.g. "acct_xxx" — stored in contractor_details.stripe_account_id


@dataclass
class AccountLinkResult:
    url:        str   # redirect the contractor to this URL
    expires_at: int   # Unix timestamp


@dataclass
class AccountStatusResult:
    account_id:        str
    charges_enabled:   bool
    payouts_enabled:   bool
    details_submitted: bool


@dataclass
class CryptoTransferResult:
    payout_id: str    # Circle payout UUID
    status:    str    # "pending" | "complete" | "failed"


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

    @abstractmethod
    async def create_connect_account(self, email: str, metadata: dict) -> ConnectAccountResult:
        """Create a new Express connected account and return its ID.

        Called the first time a contractor starts Connect onboarding.
        The account ID must be stored in contractor_details.stripe_account_id.
        """

    @abstractmethod
    async def create_account_link(
        self,
        account_id: str,
        return_url: str,
        refresh_url: str,
    ) -> AccountLinkResult:
        """Create a single-use Account Link for onboarding / re-onboarding.

        The contractor is redirected to AccountLinkResult.url.  The link
        expires after ~5 minutes; call this endpoint again to get a fresh one.
        """

    @abstractmethod
    async def get_account_status(self, account_id: str) -> AccountStatusResult:
        """Retrieve the current capabilities state of a connected account."""


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

    async def create_connect_account(self, email: str, metadata: dict) -> ConnectAccountResult:
        def _create():
            return self._stripe.Account.create(
                type="express",
                email=email,
                metadata=metadata,
            )

        account = await asyncio.to_thread(_create)
        log.info("stripe_connect_account_created", extra={"account_id": account.id})
        return ConnectAccountResult(account_id=account.id)

    async def create_account_link(
        self,
        account_id: str,
        return_url: str,
        refresh_url: str,
    ) -> AccountLinkResult:
        def _create():
            return self._stripe.AccountLink.create(
                account=account_id,
                type="account_onboarding",
                return_url=return_url,
                refresh_url=refresh_url,
            )

        link = await asyncio.to_thread(_create)
        log.info("stripe_account_link_created", extra={"account_id": account_id})
        return AccountLinkResult(url=link.url, expires_at=link.expires_at)

    async def get_account_status(self, account_id: str) -> AccountStatusResult:
        def _retrieve():
            return self._stripe.Account.retrieve(account_id)

        account = await asyncio.to_thread(_retrieve)
        return AccountStatusResult(
            account_id=account_id,
            charges_enabled=bool(account.charges_enabled),
            payouts_enabled=bool(account.payouts_enabled),
            details_submitted=bool(account.details_submitted),
        )


# ---------------------------------------------------------------------------
# Circle crypto payout provider
# ---------------------------------------------------------------------------

class CircleCryptoProvider:
    """Circle API — push USDC to an on-chain wallet address on Base.

    Requires:
      CIRCLE_API_KEY    — from Circle Developer Console → API Keys
      CIRCLE_WALLET_ID  — the Circle wallet that holds the USDC balance

    Circle docs: https://developers.circle.com/reference/payouts-create
    """

    _BASE_URL = "https://api.circle.com/v1"

    def __init__(self, api_key: str, wallet_id: str) -> None:
        self._api_key  = api_key
        self._wallet_id = wallet_id

    async def transfer_usdc_to_wallet(
        self,
        wallet_address: str,
        amount_usdc: str,       # decimal string, e.g. "150.00"
        idempotency_key: str,   # UUID — prevents double-sends on retry
    ) -> CryptoTransferResult:
        """Send USDC on Base to *wallet_address*.

        amount_usdc must be a decimal string ("150.00"), not an integer.
        idempotency_key must be a UUID; callers should derive it from the
        escrow transaction ID so retries never double-pay.
        """
        import httpx  # already a project dependency

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type":  "application/json",
        }
        body = {
            "idempotencyKey": idempotency_key,
            "source": {
                "type": "wallet",
                "id":   self._wallet_id,
            },
            "destination": {
                "type":    "blockchain",
                "address": wallet_address,
                "chain":   "BASE",
            },
            "amount": {
                "amount":   amount_usdc,
                "currency": "USD",
            },
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._BASE_URL}/payouts",
                headers=headers,
                json=body,
            )

        if not resp.is_success:
            error_body = resp.text[:400]
            log.error(
                "circle_payout_failed",
                extra={"status": resp.status_code, "body": error_body},
            )
            raise RuntimeError(
                f"Circle payout failed ({resp.status_code}): {error_body}"
            )

        data = resp.json().get("data", {})
        payout_id = data.get("id", "")
        status    = data.get("status", "pending")

        log.info(
            "circle_payout_created",
            extra={"payout_id": payout_id, "wallet": wallet_address, "amount_usdc": amount_usdc},
        )
        return CryptoTransferResult(payout_id=payout_id, status=status)


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


def get_crypto_provider() -> CircleCryptoProvider | None:
    """Return a CircleCryptoProvider if Circle is configured, else None.

    Returning None (rather than raising) lets the escrow service gracefully
    fall back to payout_pending when Circle keys are absent.
    """
    if settings.circle_api_key and settings.circle_wallet_id:
        return CircleCryptoProvider(
            api_key=settings.circle_api_key,
            wallet_id=settings.circle_wallet_id,
        )
    return None
