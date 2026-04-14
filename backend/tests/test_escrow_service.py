"""Tests for app.services.escrow_service.

Coverage
--------
Helper functions:
  _now_iso            — returns a UTC ISO-8601 string
  _get_job            — returns job dict or raises LookupError
  _get_accepted_bid   — returns bid dict or raises LookupError
  _get_transaction    — returns transaction dict or None
  _get_contractor_stripe_account — returns account_id str or None

Service functions:
  initiate()      — creates PaymentIntent, writes transaction row
  confirm_held()  — idempotent: marks held; no-op when already held / tx missing
  release()       — transfers to contractor (or flags payout_pending when no Stripe account)
  refund()        — refunds payment through provider
  get_status()    — homeowner can view; non-owner without contractor role is rejected

No real DB or Stripe calls are made — all external dependencies are patched.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.escrow_service import (
    _now_iso,
    _get_job,
    _get_accepted_bid,
    _get_transaction,
    _get_contractor_stripe_account,
    initiate,
    confirm_held,
    release,
    refund,
    get_status,
)
from app.services.payment_provider import PaymentIntentResult, RefundResult, TransferResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_db(*execute_responses):
    """Supabase mock that returns each item in execute_responses in order.

    Every chained method (table, select, insert, update, eq, limit, …) returns
    the same mock so that .execute() is always the terminal call, consuming the
    next entry from side_effect.
    """
    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.insert.return_value = db
    db.update.return_value = db
    db.delete.return_value = db
    db.eq.return_value = db
    db.limit.return_value = db
    db.execute.side_effect = [MagicMock(data=r) for r in execute_responses]
    return db


def _make_provider(*, transfer_id="tr_abc123", refund_id="re_abc123"):
    """Mock EscrowProvider with async methods pre-configured."""
    provider = MagicMock()
    provider.create_payment_intent = AsyncMock(
        return_value=PaymentIntentResult(
            client_secret="cs_test_xxx",
            provider_ref="pi_abc123",
            status="requires_payment_method",
        )
    )
    provider.transfer_to_contractor = AsyncMock(
        return_value=TransferResult(transfer_id=transfer_id)
    )
    provider.refund_payment = AsyncMock(
        return_value=RefundResult(refund_id=refund_id, status="succeeded")
    )
    return provider


# Shared test data
_JOB = {
    "id": "job-001",
    "user_id": "owner-001",
    "status": "awarded",
    "escrow_status": None,
    "title": "Fix boiler",
}
_JOB_HELD = {**_JOB, "escrow_status": "held"}

_BID = {
    "id": "bid-001",
    "job_id": "job-001",
    "contractor_id": "contractor-001",
    "amount_pence": 50_000,
    "status": "accepted",
}

_TX = {
    "id": "tx-001",
    "job_id": "job-001",
    "contractor_id": "contractor-001",
    "homeowner_id": "owner-001",
    "amount_pence": 50_000,
    "currency": "gbp",
    "provider_ref": "pi_abc123",
    "status": "held",
}
_TX_PROCESSING = {**_TX, "status": "processing"}
_TX_FAILED = {**_TX, "status": "failed"}


# ---------------------------------------------------------------------------
# _now_iso
# ---------------------------------------------------------------------------

class TestNowIso:
    def test_returns_utc_iso_string(self):
        ts = _now_iso()
        assert "T" in ts
        assert ts.endswith("+00:00")


# ---------------------------------------------------------------------------
# _get_job
# ---------------------------------------------------------------------------

class TestGetJob:
    def test_returns_job_when_found(self):
        db = _make_db([_JOB])
        assert _get_job(db, "job-001") == _JOB

    def test_raises_lookup_error_when_not_found(self):
        db = _make_db([])
        with pytest.raises(LookupError, match="job-001"):
            _get_job(db, "job-001")


# ---------------------------------------------------------------------------
# _get_accepted_bid
# ---------------------------------------------------------------------------

class TestGetAcceptedBid:
    def test_returns_bid_when_found(self):
        db = _make_db([_BID])
        assert _get_accepted_bid(db, "job-001") == _BID

    def test_raises_lookup_error_when_not_found(self):
        db = _make_db([])
        with pytest.raises(LookupError, match="No accepted bid"):
            _get_accepted_bid(db, "job-001")


# ---------------------------------------------------------------------------
# _get_transaction
# ---------------------------------------------------------------------------

class TestGetTransaction:
    def test_returns_transaction_when_found(self):
        db = _make_db([_TX])
        assert _get_transaction(db, "job-001") == _TX

    def test_returns_none_when_not_found(self):
        db = _make_db([])
        assert _get_transaction(db, "job-001") is None


# ---------------------------------------------------------------------------
# _get_contractor_stripe_account
# ---------------------------------------------------------------------------

class TestGetContractorStripeAccount:
    def test_returns_account_id_when_set(self):
        db = _make_db([{"stripe_account_id": "acct_abc"}])
        assert _get_contractor_stripe_account(db, "c-001") == "acct_abc"

    def test_returns_none_when_no_row(self):
        db = _make_db([])
        assert _get_contractor_stripe_account(db, "c-001") is None

    def test_returns_none_when_account_id_empty_string(self):
        db = _make_db([{"stripe_account_id": ""}])
        assert _get_contractor_stripe_account(db, "c-001") is None


# ---------------------------------------------------------------------------
# initiate()
# ---------------------------------------------------------------------------

class TestInitiate:
    def test_happy_path_inserts_new_transaction(self):
        # execute order: get_job, get_tx (None), get_bid, insert
        db = _make_db([_JOB], [], [_BID], [])
        provider = _make_provider()
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db), \
             patch("app.services.escrow_service.get_escrow_provider", return_value=provider):
            result = _run(initiate("job-001", "owner-001"))

        assert result["client_secret"] == "cs_test_xxx"
        assert result["provider_ref"] == "pi_abc123"
        assert result["amount_pence"] == 50_000
        assert result["currency"] == "gbp"
        db.insert.assert_called_once()

    def test_updates_existing_failed_transaction(self):
        # Failed tx → allowed to re-initiate; should UPDATE not INSERT
        db = _make_db([_JOB], [_TX_FAILED], [_BID], [])
        provider = _make_provider()
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db), \
             patch("app.services.escrow_service.get_escrow_provider", return_value=provider):
            result = _run(initiate("job-001", "owner-001"))

        assert result["client_secret"] == "cs_test_xxx"
        db.update.assert_called()
        db.insert.assert_not_called()

    def test_raises_permission_error_when_not_owner(self):
        db = _make_db([_JOB])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(PermissionError, match="Not the job owner"):
                _run(initiate("job-001", "someone-else"))

    def test_raises_value_error_when_job_not_awarded(self):
        job_open = {**_JOB, "status": "open"}
        db = _make_db([job_open])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(ValueError, match="awarded"):
                _run(initiate("job-001", "owner-001"))

    def test_raises_when_escrow_already_processing(self):
        db = _make_db([_JOB], [_TX_PROCESSING])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(ValueError, match="processing"):
                _run(initiate("job-001", "owner-001"))

    def test_raises_when_escrow_already_held(self):
        db = _make_db([_JOB], [_TX])  # _TX.status = "held"
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(ValueError, match="held"):
                _run(initiate("job-001", "owner-001"))

    def test_raises_when_escrow_released(self):
        tx_released = {**_TX, "status": "released"}
        db = _make_db([_JOB], [tx_released])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(ValueError, match="released"):
                _run(initiate("job-001", "owner-001"))


# ---------------------------------------------------------------------------
# confirm_held()
# ---------------------------------------------------------------------------

class TestConfirmHeld:
    def test_marks_transaction_and_job_as_held(self):
        # execute order: get_tx, update tx, update job
        db = _make_db([_TX_PROCESSING], [], [])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            _run(confirm_held("job-001", "pi_abc123"))

        assert db.update.call_count == 2

    def test_idempotent_when_already_held(self):
        db = _make_db([_TX])  # status already "held"
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            _run(confirm_held("job-001", "pi_abc123"))

        db.update.assert_not_called()

    def test_no_op_when_transaction_not_found(self):
        db = _make_db([])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            _run(confirm_held("job-001", "pi_abc123"))  # must not raise

        db.update.assert_not_called()


# ---------------------------------------------------------------------------
# release()
# ---------------------------------------------------------------------------

class TestRelease:
    def test_happy_path_with_stripe_account(self):
        # execute order: get_job, get_tx, get_stripe_account, update tx, update job
        db = _make_db([_JOB_HELD], [_TX], [{"stripe_account_id": "acct_abc"}], [], [])
        provider = _make_provider()
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db), \
             patch("app.services.escrow_service.get_escrow_provider", return_value=provider):
            result = _run(release("job-001", "owner-001"))

        assert result["status"] == "released"
        assert result["transfer_id"] == "tr_abc123"
        assert result["payout_pending"] is False

    def test_happy_path_without_stripe_account_sets_payout_pending(self):
        # No stripe_account_id → payout_pending flag, no provider call
        # execute order: get_job, get_tx, get_stripe_account (empty), update tx, update job
        db = _make_db([_JOB_HELD], [_TX], [], [], [])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            result = _run(release("job-001", "owner-001"))

        assert result["status"] == "released"
        assert result["transfer_id"] is None
        assert result["payout_pending"] is True

    def test_raises_permission_error_when_not_owner(self):
        db = _make_db([_JOB])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(PermissionError, match="Not the job owner"):
                _run(release("job-001", "someone-else"))

    def test_raises_value_error_when_funds_not_held(self):
        job_pending = {**_JOB, "escrow_status": "pending"}
        db = _make_db([job_pending])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(ValueError, match="not held"):
                _run(release("job-001", "owner-001"))

    def test_raises_when_no_held_transaction(self):
        db = _make_db([_JOB_HELD], [_TX_PROCESSING])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(ValueError, match="No held escrow"):
                _run(release("job-001", "owner-001"))


# ---------------------------------------------------------------------------
# refund()
# ---------------------------------------------------------------------------

class TestRefund:
    def test_happy_path(self):
        # execute order: get_job, get_tx, update tx, update job
        db = _make_db([_JOB_HELD], [_TX], [], [])
        provider = _make_provider()
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db), \
             patch("app.services.escrow_service.get_escrow_provider", return_value=provider):
            result = _run(refund("job-001", "owner-001"))

        assert result["status"] == "refunded"
        assert result["refund_id"] == "re_abc123"

    def test_happy_path_with_reason(self):
        db = _make_db([_JOB_HELD], [_TX], [], [])
        provider = _make_provider()
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db), \
             patch("app.services.escrow_service.get_escrow_provider", return_value=provider):
            result = _run(refund("job-001", "owner-001", reason="work not completed"))

        assert result["status"] == "refunded"
        provider.refund_payment.assert_called_once_with(
            payment_intent_id="pi_abc123",
            reason="work not completed",
        )

    def test_default_reason_when_none_given(self):
        db = _make_db([_JOB_HELD], [_TX], [], [])
        provider = _make_provider()
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db), \
             patch("app.services.escrow_service.get_escrow_provider", return_value=provider):
            _run(refund("job-001", "owner-001"))

        provider.refund_payment.assert_called_once_with(
            payment_intent_id="pi_abc123",
            reason="requested_by_customer",
        )

    def test_raises_permission_error_when_not_owner(self):
        db = _make_db([_JOB])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(PermissionError, match="Not the job owner"):
                _run(refund("job-001", "someone-else"))

    def test_raises_when_escrow_not_held(self):
        job_pending = {**_JOB, "escrow_status": "pending"}
        db = _make_db([job_pending])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(ValueError, match="Cannot refund"):
                _run(refund("job-001", "owner-001"))

    def test_raises_when_no_held_transaction(self):
        db = _make_db([_JOB_HELD], [_TX_PROCESSING])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(ValueError, match="No held escrow"):
                _run(refund("job-001", "owner-001"))


# ---------------------------------------------------------------------------
# get_status()
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_homeowner_can_view_status(self):
        # execute order: get_job, get_tx (final call)
        db = _make_db([_JOB], [_TX])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            result = _run(get_status("job-001", "owner-001"))

        assert result["job_id"] == "job-001"
        assert result["transaction"] == _TX

    def test_job_not_found_raises_lookup_error(self):
        db = _make_db([])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(LookupError):
                _run(get_status("job-001", "owner-001"))

    def test_unrelated_user_raises_permission_error(self):
        # execute order: get_job, get_tx (in if block), contractor lookup (empty)
        db = _make_db([_JOB], [_TX], [])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            with pytest.raises(PermissionError, match="Not authorised"):
                _run(get_status("job-001", "stranger-999"))

    def test_escrow_status_reflected_in_response(self):
        job_released = {**_JOB, "escrow_status": "funds_released"}
        tx_released = {**_TX, "status": "released"}
        db = _make_db([job_released], [tx_released])
        with patch("app.services.escrow_service.get_supabase_admin", return_value=db):
            result = _run(get_status("job-001", "owner-001"))

        assert result["job_escrow_status"] == "funds_released"
        assert result["transaction"]["status"] == "released"
