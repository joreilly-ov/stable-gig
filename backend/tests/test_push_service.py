"""Tests for the push notification service.

Coverage
--------
notify_contractors_of_new_job
  - skips silently when VAPID is not configured
  - skips when no contractors match the job activity
  - skips when matching contractors have no push subscriptions
  - sends notifications to all subscribed contractors
  - removes dead subscriptions (failed sends) without failing the caller
  - uses contractor.id (not user_id) per Clean Split identity design

No real push sends or DB calls are made — all external dependencies are patched.
"""

import asyncio
from unittest.mock import MagicMock, patch, call

import pytest

import app.services.push_service as push_svc
from app.services.push_service import notify_contractors_of_new_job, _vapid_configured

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_JOB = {
    "id":       "job-001",
    "title":    "Leaky tap",
    "activity": "plumbing",
    "postcode": "SW1A 1AA",
}

_CONTRACTOR_ROW_1 = {"id": "user-001"}  # Clean Split: id = auth.users.id
_CONTRACTOR_ROW_2 = {"id": "user-002"}

_SUB_1 = {
    "id":        "sub-001",
    "user_id":   "user-001",
    "endpoint":  "https://push.example.com/sub/aaa",
    "p256dh":    "BNFMy...key1",
    "auth_key":  "auth1",
}
_SUB_2 = {
    "id":        "sub-002",
    "user_id":   "user-002",
    "endpoint":  "https://push.example.com/sub/bbb",
    "p256dh":    "BNFMy...key2",
    "auth_key":  "auth2",
}


def _make_db(contractors=None, subscriptions=None):
    """Return a Supabase-mock that returns preset data in sequence."""
    db = MagicMock()
    db.table.return_value    = db
    db.select.return_value   = db
    db.delete.return_value   = db
    db.contains.return_value = db
    db.in_.return_value      = db
    db.eq.return_value       = db
    db.execute.side_effect = [
        MagicMock(data=contractors if contractors is not None else []),
        MagicMock(data=subscriptions if subscriptions is not None else []),
    ]
    return db


def _configured_settings():
    s = MagicMock()
    s.vapid_private_key   = "private-key-base64url"
    s.vapid_public_key    = "public-key-base64url"
    s.vapid_claims_email  = "mailto:admin@example.com"
    return s


def _unconfigured_settings():
    s = MagicMock()
    s.vapid_private_key   = ""
    s.vapid_public_key    = ""
    s.vapid_claims_email  = ""
    return s


# ---------------------------------------------------------------------------
# _vapid_configured helper
# ---------------------------------------------------------------------------

class TestVapidConfigured:
    def test_true_when_all_keys_set(self):
        with patch.object(push_svc, "settings", _configured_settings()):
            assert _vapid_configured() is True

    def test_false_when_private_key_missing(self):
        s = _configured_settings()
        s.vapid_private_key = ""
        with patch.object(push_svc, "settings", s):
            assert _vapid_configured() is False

    def test_false_when_all_keys_missing(self):
        with patch.object(push_svc, "settings", _unconfigured_settings()):
            assert _vapid_configured() is False


# ---------------------------------------------------------------------------
# notify_contractors_of_new_job
# ---------------------------------------------------------------------------

class TestNotifyContractors:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_skips_when_vapid_not_configured(self):
        # Reset the "warned once" flag for a clean test
        push_svc._MISSING_VAPID_WARNED = False
        db = MagicMock()

        with patch.object(push_svc, "settings", _unconfigured_settings()), \
             patch("app.services.push_service.get_supabase_admin", return_value=db):
            self._run(notify_contractors_of_new_job(_JOB))

        # DB should never be touched
        db.table.assert_not_called()

    def test_skips_when_no_matching_contractors(self):
        db = _make_db(contractors=[])  # empty contractor list

        with patch.object(push_svc, "settings", _configured_settings()), \
             patch("app.services.push_service.get_supabase_admin", return_value=db):
            self._run(notify_contractors_of_new_job(_JOB))

        # Only the contractor lookup should have been called, not the subscription lookup
        assert db.execute.call_count == 1

    def test_skips_when_no_subscriptions(self):
        db = _make_db(
            contractors=[_CONTRACTOR_ROW_1],
            subscriptions=[],            # no push subscriptions
        )

        with patch.object(push_svc, "settings", _configured_settings()), \
             patch("app.services.push_service.get_supabase_admin", return_value=db), \
             patch.object(push_svc, "_send_one") as mock_send:
            self._run(notify_contractors_of_new_job(_JOB))

        mock_send.assert_not_called()

    def test_sends_to_all_subscribed_contractors(self):
        db = _make_db(
            contractors=[_CONTRACTOR_ROW_1, _CONTRACTOR_ROW_2],
            subscriptions=[_SUB_1, _SUB_2],
        )

        with patch.object(push_svc, "settings", _configured_settings()), \
             patch("app.services.push_service.get_supabase_admin", return_value=db), \
             patch.object(push_svc, "_send_one", return_value=True) as mock_send:
            self._run(notify_contractors_of_new_job(_JOB))

        assert mock_send.call_count == 2
        # Verify payload shape on first call
        first_call_sub, first_call_payload = mock_send.call_args_list[0][0]
        assert first_call_payload["type"] == "new_job"
        assert first_call_payload["job_id"] == "job-001"
        assert "plumbing" in first_call_payload["body"].lower()

    def test_uses_contractor_id_not_user_id(self):
        """Under Clean Split, contractors.id is the user identity — no user_id column."""
        db = _make_db(
            contractors=[{"id": "user-abc"}],  # id only, no user_id
            subscriptions=[{**_SUB_1, "user_id": "user-abc"}],
        )

        with patch.object(push_svc, "settings", _configured_settings()), \
             patch("app.services.push_service.get_supabase_admin", return_value=db), \
             patch.object(push_svc, "_send_one", return_value=True) as mock_send:
            self._run(notify_contractors_of_new_job(_JOB))

        # "user-abc" should appear in the in_ call for subscriptions lookup
        mock_send.assert_called_once()

    def test_removes_dead_subscription_on_failure(self):
        """Failed sends trigger cleanup of the dead subscription row."""
        db = MagicMock()
        db.table.return_value    = db
        db.select.return_value   = db
        db.delete.return_value   = db
        db.contains.return_value = db
        db.in_.return_value      = db
        db.eq.return_value       = db
        # Three execute calls: contractor query, subscription query, delete
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW_1]),
            MagicMock(data=[_SUB_1]),
            MagicMock(data=[]),  # delete response
        ]

        with patch.object(push_svc, "settings", _configured_settings()), \
             patch("app.services.push_service.get_supabase_admin", return_value=db), \
             patch.object(push_svc, "_send_one", return_value=False):
            self._run(notify_contractors_of_new_job(_JOB))

        # delete() should have been called (not just table → select)
        db.delete.assert_called_once()
