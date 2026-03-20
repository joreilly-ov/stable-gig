"""Unit tests for app/services/usage_logger.py.

get_supabase_admin is imported lazily inside log_usage() with
`from app.database import get_supabase_admin`, so all patches
must target app.database.get_supabase_admin (not the logger module).

Coverage
--------
- Correct row inserted into usage_log table
- user_id=None is passed through (unauthenticated callers)
- analysis_type and model fields set correctly
- Never raises even if the database call throws
- Never raises when execute() itself throws
- Warning is logged on failure
"""

import logging
from unittest.mock import MagicMock, patch

from app.services.usage_logger import log_usage

_DB_PATCH = "app.database.get_supabase_admin"


def _make_admin_mock():
    mock = MagicMock()
    mock.table.return_value.insert.return_value.execute.return_value = MagicMock()
    return mock


class TestLogUsage:
    def test_inserts_correct_row(self):
        admin = _make_admin_mock()
        with patch(_DB_PATCH, return_value=admin):
            log_usage(
                analysis_type="video",
                model="gemini-2.5-flash",
                user_id="user-abc",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
            )

        admin.table.assert_called_once_with("usage_log")
        admin.table.return_value.insert.assert_called_once_with({
            "analysis_type": "video",
            "model": "gemini-2.5-flash",
            "user_id": "user-abc",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        })
        admin.table.return_value.insert.return_value.execute.assert_called_once()

    def test_none_user_id_passed_through(self):
        admin = _make_admin_mock()
        with patch(_DB_PATCH, return_value=admin):
            log_usage("photo", "gemini-1.5-flash", None, 10, 5, 15)

        inserted = admin.table.return_value.insert.call_args[0][0]
        assert inserted["user_id"] is None

    def test_photo_analysis_type(self):
        admin = _make_admin_mock()
        with patch(_DB_PATCH, return_value=admin):
            log_usage("photo", "gemini-1.5-flash", "u1", 0, 0, 0)

        inserted = admin.table.return_value.insert.call_args[0][0]
        assert inserted["analysis_type"] == "photo"
        assert inserted["model"] == "gemini-1.5-flash"

    def test_never_raises_on_db_error(self):
        """A database failure must not propagate — log_usage is fire-and-forget."""
        with patch(_DB_PATCH, side_effect=RuntimeError("db is down")):
            log_usage("video", "gemini-2.5-flash", None, 0, 0, 0)  # must not raise

    def test_never_raises_on_execute_error(self):
        admin = _make_admin_mock()
        admin.table.return_value.insert.return_value.execute.side_effect = Exception("timeout")
        with patch(_DB_PATCH, return_value=admin):
            log_usage("video", "gemini-2.5-flash", "u1", 1, 2, 3)  # must not raise

    def test_logs_warning_on_failure(self, caplog):
        with (
            patch(_DB_PATCH, side_effect=RuntimeError("boom")),
            caplog.at_level(logging.WARNING, logger="app.services.usage_logger"),
        ):
            log_usage("video", "gemini-2.5-flash", "u1", 1, 2, 3)

        assert any("usage_log_failed" in r.message for r in caplog.records)
