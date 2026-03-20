"""Unit tests for app/database.py.

The module holds two module-level singleton globals (_client, _admin_client).
Each test resets them via an autouse fixture to ensure isolation.

Coverage
--------
get_supabase():
  - Raises RuntimeError when SUPABASE_URL is missing
  - Raises RuntimeError when SUPABASE_ANON_KEY is missing
  - Returns a client created by create_client on first call
  - Returns the same cached instance on subsequent calls (singleton)

get_supabase_admin():
  - Raises RuntimeError when SUPABASE_URL is missing
  - Raises RuntimeError when SUPABASE_SERVICE_KEY is missing
  - Returns a client created by create_client on first call
  - Returns the same cached instance on subsequent calls (singleton)
"""

from unittest.mock import MagicMock, call, patch

import pytest

import app.database as db_module
from app.database import get_supabase, get_supabase_admin


# ---------------------------------------------------------------------------
# Reset singletons between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singletons():
    old_client = db_module._client
    old_admin = db_module._admin_client
    db_module._client = None
    db_module._admin_client = None
    yield
    db_module._client = old_client
    db_module._admin_client = old_admin


# ---------------------------------------------------------------------------
# get_supabase
# ---------------------------------------------------------------------------

class TestGetSupabase:
    def test_raises_when_url_missing(self):
        with patch("app.database.settings") as mock_s:
            mock_s.supabase_url = ""
            mock_s.supabase_anon_key = "anon-key"
            with pytest.raises(RuntimeError, match="SUPABASE_URL"):
                get_supabase()

    def test_raises_when_anon_key_missing(self):
        with patch("app.database.settings") as mock_s:
            mock_s.supabase_url = "https://test.supabase.co"
            mock_s.supabase_anon_key = ""
            with pytest.raises(RuntimeError, match="SUPABASE_ANON_KEY"):
                get_supabase()

    def test_creates_client_with_correct_args(self):
        mock_client = MagicMock()
        with (
            patch("app.database.settings") as mock_s,
            patch("app.database.create_client", return_value=mock_client) as mock_create,
        ):
            mock_s.supabase_url = "https://test.supabase.co"
            mock_s.supabase_anon_key = "anon-key-abc"
            result = get_supabase()

        mock_create.assert_called_once_with("https://test.supabase.co", "anon-key-abc")
        assert result is mock_client

    def test_singleton_returns_same_instance(self):
        mock_client = MagicMock()
        with (
            patch("app.database.settings") as mock_s,
            patch("app.database.create_client", return_value=mock_client) as mock_create,
        ):
            mock_s.supabase_url = "https://test.supabase.co"
            mock_s.supabase_anon_key = "anon-key-abc"
            first = get_supabase()
            second = get_supabase()

        assert first is second
        assert mock_create.call_count == 1   # created only once


# ---------------------------------------------------------------------------
# get_supabase_admin
# ---------------------------------------------------------------------------

class TestGetSupabaseAdmin:
    def test_raises_when_url_missing(self):
        with patch("app.database.settings") as mock_s:
            mock_s.supabase_url = ""
            mock_s.supabase_service_key = "service-key"
            with pytest.raises(RuntimeError, match="SUPABASE_URL"):
                get_supabase_admin()

    def test_raises_when_service_key_missing(self):
        with patch("app.database.settings") as mock_s:
            mock_s.supabase_url = "https://test.supabase.co"
            mock_s.supabase_service_key = ""
            with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_KEY"):
                get_supabase_admin()

    def test_creates_admin_client_with_service_key(self):
        mock_admin = MagicMock()
        with (
            patch("app.database.settings") as mock_s,
            patch("app.database.create_client", return_value=mock_admin) as mock_create,
        ):
            mock_s.supabase_url = "https://test.supabase.co"
            mock_s.supabase_service_key = "service-key-xyz"
            result = get_supabase_admin()

        mock_create.assert_called_once_with("https://test.supabase.co", "service-key-xyz")
        assert result is mock_admin

    def test_singleton_returns_same_instance(self):
        mock_admin = MagicMock()
        with (
            patch("app.database.settings") as mock_s,
            patch("app.database.create_client", return_value=mock_admin) as mock_create,
        ):
            mock_s.supabase_url = "https://test.supabase.co"
            mock_s.supabase_service_key = "service-key-xyz"
            first = get_supabase_admin()
            second = get_supabase_admin()

        assert first is second
        assert mock_create.call_count == 1

    def test_anon_and_admin_clients_are_independent(self):
        anon_mock = MagicMock()
        admin_mock = MagicMock()
        call_count = 0

        def _create(url, key):
            nonlocal call_count
            call_count += 1
            return anon_mock if call_count == 1 else admin_mock

        with (
            patch("app.database.settings") as mock_s,
            patch("app.database.create_client", side_effect=_create),
        ):
            mock_s.supabase_url = "https://test.supabase.co"
            mock_s.supabase_anon_key = "anon"
            mock_s.supabase_service_key = "service"
            anon = get_supabase()
            admin = get_supabase_admin()

        assert anon is not admin
