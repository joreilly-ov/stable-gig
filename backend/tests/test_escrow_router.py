"""
Tests for the escrow router.

Coverage
--------
GET  /escrow/config
  - 200 returns stripe_publishable_key
  - 503 when key not configured

POST /jobs/{job_id}/escrow/initiate
  - 200 returns client_secret, provider_ref, amount_pence, currency, publishable key
  - 404 when job not found
  - 403 when caller is not the job owner
  - 422 when job is not in 'awarded' status
  - 422 when escrow already active
  - 503 when payment provider not configured
  - 401 when unauthenticated

GET  /jobs/{job_id}/escrow
  - 200 returns job_escrow_status and transaction
  - 404 when job not found
  - 403 when non-owner non-contractor calls
  - 401 when unauthenticated

POST /jobs/{job_id}/escrow/release
  - 200 returns status=released and transfer_id
  - 200 payout_pending=true when contractor has no Stripe account
  - 422 when escrow not held
  - 403 when non-owner
  - 401 when unauthenticated

POST /jobs/{job_id}/escrow/refund
  - 200 returns status=refunded and refund_id
  - 422 when escrow not held
  - 401 when unauthenticated

POST /webhooks/stripe
  - 200 on valid payment_intent.succeeded → confirm_held called
  - 200 on valid payment_intent.payment_failed
  - 400 on invalid signature
  - 200 on event with no job_id metadata (ignored gracefully)

No real DB, Stripe, or network calls — everything is patched.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Stub stripe before any app module is imported (mirrors conftest pattern)
sys.modules.setdefault("stripe", MagicMock())

from app.routers.escrow import router as escrow_router
from app.dependencies import get_current_user

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(escrow_router)

_OWNER_ID      = "aaaaaaaa-0000-0000-0000-000000000001"
_CONTRACTOR_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_OTHER_ID      = "cccccccc-0000-0000-0000-000000000003"
_JOB_ID        = "dddddddd-0000-0000-0000-000000000004"

_OWNER_USER = SimpleNamespace(id=_OWNER_ID)
_OTHER_USER = SimpleNamespace(id=_OTHER_ID)

_AWARDED_JOB = {
    "id":            _JOB_ID,
    "user_id":       _OWNER_ID,
    "title":         "Bathroom leak",
    "status":        "awarded",
    "escrow_status": "pending",
    "activity":      "plumbing",
    "postcode":      "SW1A 1AA",
}

_HELD_JOB = {**_AWARDED_JOB, "status": "in_progress", "escrow_status": "held"}
_OPEN_JOB  = {**_AWARDED_JOB, "status": "open", "escrow_status": "pending"}

_STUB_INITIATE = {
    "client_secret":          "pi_test_secret_xyz",
    "provider_ref":           "pi_test_xxx",
    "amount_pence":           45000,
    "currency":               "gbp",
    "stripe_publishable_key": "pk_test_xxx",
}

_STUB_STATUS = {
    "job_id":             _JOB_ID,
    "job_escrow_status":  "held",
    "transaction": {
        "id":           "tx-001",
        "job_id":       _JOB_ID,
        "status":       "held",
        "amount_pence": 45000,
        "currency":     "gbp",
        "provider_ref": "pi_test_xxx",
    },
}

_STUB_RELEASE = {"status": "released", "transfer_id": "tr_test_xxx", "payout_pending": False}
_STUB_RELEASE_PENDING = {"status": "released", "transfer_id": None, "payout_pending": True}
_STUB_REFUND  = {"status": "refunded", "refund_id": "re_test_xxx"}


# ---------------------------------------------------------------------------
# GET /escrow/config
# ---------------------------------------------------------------------------

def test_escrow_config_returns_publishable_key():
    with patch("app.routers.escrow.settings") as mock_settings:
        mock_settings.stripe_publishable_key = "pk_test_xxx"
        client = TestClient(app)
        resp = client.get("/escrow/config")
    assert resp.status_code == 200
    assert resp.json()["stripe_publishable_key"] == "pk_test_xxx"


def test_escrow_config_503_when_not_configured():
    with patch("app.routers.escrow.settings") as mock_settings:
        mock_settings.stripe_publishable_key = ""
        client = TestClient(app)
        resp = client.get("/escrow/config")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/escrow/initiate
# ---------------------------------------------------------------------------

def test_initiate_200():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.initiate", new=AsyncMock(return_value=_STUB_INITIATE)):
        resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/initiate")
    assert resp.status_code == 200
    data = resp.json()
    assert "client_secret" in data
    assert data["amount_pence"] == 45000
    assert data["currency"] == "gbp"
    app.dependency_overrides.clear()


def test_initiate_response_shape():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.initiate", new=AsyncMock(return_value=_STUB_INITIATE)):
        data = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/initiate").json()
    assert data["client_secret"] == "pi_test_secret_xyz"
    assert data["provider_ref"]  == "pi_test_xxx"
    assert data["stripe_publishable_key"] == "pk_test_xxx"
    app.dependency_overrides.clear()


def test_initiate_404_job_not_found():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.initiate", side_effect=LookupError("Job not found")):
        resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/initiate")
    assert resp.status_code == 404
    app.dependency_overrides.clear()


def test_initiate_403_non_owner():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.initiate", side_effect=PermissionError("Not the job owner")):
        resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/initiate")
    assert resp.status_code == 403
    app.dependency_overrides.clear()


def test_initiate_422_wrong_status():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.initiate", side_effect=ValueError("Job must be in 'awarded' status")):
        resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/initiate")
    assert resp.status_code == 422
    app.dependency_overrides.clear()


def test_initiate_422_already_held():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.initiate", side_effect=ValueError("Escrow already in status 'held'")):
        resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/initiate")
    assert resp.status_code == 422
    app.dependency_overrides.clear()


def test_initiate_503_provider_not_configured():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.initiate", side_effect=RuntimeError("Payment provider not configured")):
        resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/initiate")
    assert resp.status_code == 503
    app.dependency_overrides.clear()


def test_initiate_requires_auth():
    resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/initiate")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/escrow
# ---------------------------------------------------------------------------

def test_get_status_200():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.get_status", new=AsyncMock(return_value=_STUB_STATUS)):
        resp = TestClient(app).get(f"/jobs/{_JOB_ID}/escrow")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_escrow_status"] == "held"
    assert data["transaction"]["amount_pence"] == 45000
    app.dependency_overrides.clear()


def test_get_status_404():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.get_status", side_effect=LookupError("Job not found")):
        resp = TestClient(app).get(f"/jobs/{_JOB_ID}/escrow")
    assert resp.status_code == 404
    app.dependency_overrides.clear()


def test_get_status_403():
    app.dependency_overrides[get_current_user] = lambda: _OTHER_USER
    with patch("app.routers.escrow.escrow_service.get_status", side_effect=PermissionError("Not authorised")):
        resp = TestClient(app).get(f"/jobs/{_JOB_ID}/escrow")
    assert resp.status_code == 403
    app.dependency_overrides.clear()


def test_get_status_requires_auth():
    resp = TestClient(app).get(f"/jobs/{_JOB_ID}/escrow")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/escrow/release
# ---------------------------------------------------------------------------

def test_release_200():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.release", new=AsyncMock(return_value=_STUB_RELEASE)):
        resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/release", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "released"
    assert data["transfer_id"] == "tr_test_xxx"
    assert data["payout_pending"] is False
    app.dependency_overrides.clear()


def test_release_200_payout_pending():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.release", new=AsyncMock(return_value=_STUB_RELEASE_PENDING)):
        data = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/release", json={}).json()
    assert data["payout_pending"] is True
    assert data["transfer_id"] is None
    app.dependency_overrides.clear()


def test_release_passes_note_to_service():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    mock_release = AsyncMock(return_value=_STUB_RELEASE)
    with patch("app.routers.escrow.escrow_service.release", new=mock_release):
        TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/release", json={"note": "Work looks great"})
    assert mock_release.call_args.kwargs["note"] == "Work looks great"
    app.dependency_overrides.clear()


def test_release_422_not_held():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.release", side_effect=ValueError("Funds are not held")):
        resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/release", json={})
    assert resp.status_code == 422
    app.dependency_overrides.clear()


def test_release_requires_auth():
    resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/release", json={})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/escrow/refund
# ---------------------------------------------------------------------------

def test_refund_200():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.refund", new=AsyncMock(return_value=_STUB_REFUND)):
        resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/refund", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "refunded"
    assert data["refund_id"] == "re_test_xxx"
    app.dependency_overrides.clear()


def test_refund_422_not_held():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    with patch("app.routers.escrow.escrow_service.refund", side_effect=ValueError("Cannot refund — escrow status is 'pending'")):
        resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/refund", json={})
    assert resp.status_code == 422
    app.dependency_overrides.clear()


def test_refund_requires_auth():
    resp = TestClient(app).post(f"/jobs/{_JOB_ID}/escrow/refund", json={})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /webhooks/stripe
# ---------------------------------------------------------------------------

def _make_webhook_event(event_type: str, job_id: str | None = _JOB_ID) -> dict:
    return {
        "type": event_type,
        "data": {
            "object": {
                "id":     "pi_test_xxx",
                "status": "succeeded",
                "last_payment_error": {"message": "card declined"},
                "metadata": {"job_id": job_id} if job_id else {},
            }
        },
    }


def _make_provider_mock(event: dict | None = None, invalid_sig: bool = False) -> MagicMock:
    mock = MagicMock()
    if invalid_sig:
        mock.verify_webhook.side_effect = ValueError("bad sig")
    else:
        mock.verify_webhook.return_value = event or _make_webhook_event("payment_intent.succeeded")
    return mock


def test_webhook_payment_succeeded_calls_confirm_held():
    mock_confirm = AsyncMock()
    mock_provider = _make_provider_mock(_make_webhook_event("payment_intent.succeeded"))
    with patch("app.routers.escrow.get_escrow_provider", return_value=mock_provider), \
         patch("app.routers.escrow.escrow_service.confirm_held", new=mock_confirm):
        resp = TestClient(app).post(
            "/webhooks/stripe",
            content=b'{"type":"payment_intent.succeeded"}',
            headers={"stripe-signature": "t=xxx,v1=yyy"},
        )
    assert resp.status_code == 200
    mock_confirm.assert_awaited_once()
    assert mock_confirm.call_args.kwargs["job_id"] == _JOB_ID


def test_webhook_payment_failed_updates_db():
    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    mock_provider = _make_provider_mock(_make_webhook_event("payment_intent.payment_failed"))
    with patch("app.routers.escrow.get_escrow_provider", return_value=mock_provider), \
         patch("app.routers.escrow.get_supabase_admin", return_value=mock_db):
        resp = TestClient(app).post(
            "/webhooks/stripe",
            content=b'{}',
            headers={"stripe-signature": "t=xxx,v1=yyy"},
        )
    assert resp.status_code == 200
    mock_db.table.assert_called_with("escrow_transactions")


def test_webhook_400_invalid_signature():
    mock_provider = _make_provider_mock(invalid_sig=True)
    with patch("app.routers.escrow.get_escrow_provider", return_value=mock_provider):
        resp = TestClient(app).post(
            "/webhooks/stripe",
            content=b'{}',
            headers={"stripe-signature": "bad"},
        )
    assert resp.status_code == 400


def test_webhook_200_no_job_id_in_metadata():
    """Events not related to a job should be acknowledged and ignored."""
    mock_confirm = AsyncMock()
    mock_provider = _make_provider_mock(_make_webhook_event("payment_intent.succeeded", job_id=None))
    with patch("app.routers.escrow.get_escrow_provider", return_value=mock_provider), \
         patch("app.routers.escrow.escrow_service.confirm_held", new=mock_confirm):
        resp = TestClient(app).post(
            "/webhooks/stripe",
            content=b'{}',
            headers={"stripe-signature": "t=xxx,v1=yyy"},
        )
    assert resp.status_code == 200
    mock_confirm.assert_not_awaited()
