"""Unit tests for app/dependencies.py.

Coverage
--------
get_current_user:
  - 401 when no credentials provided
  - Returns user when token is valid
  - 401 when token is invalid / Supabase raises

get_optional_user:
  - Returns None when no credentials provided
  - Returns user when token is valid
  - Returns None (not 401) when token is invalid

_verify_token:
  - Returns user object on success
  - Raises 401 HTTPException on Supabase failure
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.dependencies import _verify_token, get_current_user, get_optional_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _creds(token: str = "valid-token") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _supabase_returning(user):
    sb = MagicMock()
    sb.auth.get_user.return_value.user = user
    return sb


# ---------------------------------------------------------------------------
# _verify_token (pure unit)
# ---------------------------------------------------------------------------

class TestVerifyToken:
    def test_returns_user_on_valid_token(self):
        mock_user = MagicMock()
        sb = _supabase_returning(mock_user)

        with patch("app.dependencies.get_supabase", return_value=sb):
            result = _verify_token("valid-token")

        assert result is mock_user

    def test_raises_401_on_supabase_exception(self):
        sb = MagicMock()
        sb.auth.get_user.side_effect = Exception("invalid JWT")

        with patch("app.dependencies.get_supabase", return_value=sb):
            with pytest.raises(HTTPException) as exc_info:
                _verify_token("bad-token")

        assert exc_info.value.status_code == 401
        assert "Invalid or expired token" in exc_info.value.detail


# ---------------------------------------------------------------------------
# get_current_user (async dependency)
# ---------------------------------------------------------------------------

class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_no_credentials_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self):
        mock_user = MagicMock()
        sb = _supabase_returning(mock_user)

        with patch("app.dependencies.get_supabase", return_value=sb):
            result = await get_current_user(credentials=_creds("good-tok"))

        assert result is mock_user

    @pytest.mark.asyncio
    async def test_invalid_token_raises_401(self):
        sb = MagicMock()
        sb.auth.get_user.side_effect = Exception("expired")

        with patch("app.dependencies.get_supabase", return_value=sb):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(credentials=_creds("bad-tok"))

        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# get_optional_user (async dependency)
# ---------------------------------------------------------------------------

class TestGetOptionalUser:
    @pytest.mark.asyncio
    async def test_no_credentials_returns_none(self):
        result = await get_optional_user(credentials=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self):
        mock_user = MagicMock()
        sb = _supabase_returning(mock_user)

        with patch("app.dependencies.get_supabase", return_value=sb):
            result = await get_optional_user(credentials=_creds("good-tok"))

        assert result is mock_user

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none_not_401(self):
        """Optional auth should swallow errors and return None, not raise."""
        sb = MagicMock()
        sb.auth.get_user.side_effect = Exception("expired")

        with patch("app.dependencies.get_supabase", return_value=sb):
            result = await get_optional_user(credentials=_creds("bad-tok"))

        assert result is None
