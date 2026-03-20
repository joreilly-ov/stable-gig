"""Integration tests for the auth router (/auth/...).

conftest.py injects a fake `main` module stub so that auth.py's
module-level `from main import limiter` resolves to a pass-through
decorator — no real rate-limiting occurs in these tests.

Coverage
--------
GET  /auth/config
  - Returns supabase_url and anon_key

POST /auth/magic-link
  - 202 on success
  - 400 when Supabase raises

POST /auth/verify
  - 200 + access_token + user_id on success
  - 400 when Supabase raises
  - 400 when session is absent

POST /auth/register
  - 422 when password is too short (< 8 chars)
  - 201 + status=active when session returned
  - 201 + status=confirmation_required when no session
  - 400 when no user returned
  - 400 when Supabase raises

POST /auth/login/password
  - 200 + access_token on success
  - 401 when Supabase raises (wrong credentials)
  - 401 when session absent

POST /auth/forgot-password
  - Always 202, even when Supabase raises (enumeration defence)

POST /auth/reset-password
  - 422 when new_password too short
  - 200 + access_token on success
  - 400 when Supabase raises
  - 400 when no user returned
  - 400 when session not established after update
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# conftest.py has already inserted a fake `main` stub before this module
# is imported, so `from main import limiter` in auth.py resolves safely.
from app.routers.auth import router

app = FastAPI()
app.include_router(router)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _supabase_mock():
    return MagicMock()


def _session(access_token="tok-abc", user_id="uid-123"):
    sess = MagicMock()
    sess.access_token = access_token
    sess.user.id = user_id
    return sess


def _auth_response(session=None, user=None):
    resp = MagicMock()
    resp.session = session
    resp.user = user or (session.user if session else None)
    return resp


# ---------------------------------------------------------------------------
# GET /auth/config
# ---------------------------------------------------------------------------

class TestAuthConfig:
    def test_returns_supabase_url_and_anon_key(self):
        with patch("app.routers.auth.settings") as mock_settings:
            mock_settings.supabase_url = "https://test.supabase.co"
            mock_settings.supabase_anon_key = "anon-key-xyz"
            resp = TestClient(app).get("/auth/config")

        assert resp.status_code == 200
        body = resp.json()
        assert body["supabase_url"] == "https://test.supabase.co"
        assert body["anon_key"] == "anon-key-xyz"


# ---------------------------------------------------------------------------
# POST /auth/magic-link
# ---------------------------------------------------------------------------

class TestMagicLink:
    def test_success_returns_202(self):
        sb = _supabase_mock()
        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/magic-link", json={"email": "user@example.com"}
            )
        assert resp.status_code == 202
        assert "user@example.com" in resp.json()["message"]

    def test_supabase_error_returns_400(self):
        sb = _supabase_mock()
        sb.auth.sign_in_with_otp.side_effect = Exception("OTP service down")
        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/magic-link", json={"email": "user@example.com"}
            )
        assert resp.status_code == 400

    def test_invalid_email_returns_422(self):
        resp = TestClient(app).post(
            "/auth/magic-link", json={"email": "not-an-email"}
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /auth/verify
# ---------------------------------------------------------------------------

class TestVerifyOtp:
    def test_success_returns_token(self):
        sess = _session("access-tok", "user-id-1")
        sb = _supabase_mock()
        sb.auth.verify_otp.return_value = _auth_response(session=sess)

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/verify",
                json={"email": "user@example.com", "token": "123456"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "access-tok"
        assert body["user_id"] == "user-id-1"

    def test_supabase_error_returns_400(self):
        sb = _supabase_mock()
        sb.auth.verify_otp.side_effect = Exception("invalid OTP")
        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/verify",
                json={"email": "user@example.com", "token": "bad"},
            )
        assert resp.status_code == 400

    def test_no_session_returns_400(self):
        sb = _supabase_mock()
        sb.auth.verify_otp.return_value = _auth_response(session=None, user=MagicMock())
        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/verify",
                json={"email": "user@example.com", "token": "123456"},
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------

class TestRegister:
    def test_short_password_returns_422(self):
        resp = TestClient(app).post(
            "/auth/register", json={"email": "user@example.com", "password": "short"}
        )
        assert resp.status_code == 422

    def test_success_with_session_returns_active(self):
        sess = _session("tok-reg", "uid-reg")
        sb = _supabase_mock()
        sb.auth.sign_up.return_value = _auth_response(session=sess)

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/register",
                json={"email": "new@example.com", "password": "password123"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "active"
        assert body["access_token"] == "tok-reg"

    def test_success_without_session_returns_confirmation_required(self):
        user = MagicMock()
        user.id = "uid-conf"
        sb = _supabase_mock()
        sb.auth.sign_up.return_value = _auth_response(session=None, user=user)

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/register",
                json={"email": "new@example.com", "password": "password123"},
            )
        assert resp.status_code == 201
        assert resp.json()["status"] == "confirmation_required"

    def test_no_user_in_response_returns_400(self):
        sb = _supabase_mock()
        sb.auth.sign_up.return_value = _auth_response(session=None, user=None)

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/register",
                json={"email": "new@example.com", "password": "password123"},
            )
        assert resp.status_code == 400

    def test_supabase_error_returns_400(self):
        sb = _supabase_mock()
        sb.auth.sign_up.side_effect = Exception("duplicate email")

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/register",
                json={"email": "dup@example.com", "password": "password123"},
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /auth/login/password
# ---------------------------------------------------------------------------

class TestLoginPassword:
    def test_success_returns_token(self):
        sess = _session("login-tok", "uid-login")
        sb = _supabase_mock()
        sb.auth.sign_in_with_password.return_value = _auth_response(session=sess)

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/login/password",
                json={"email": "user@example.com", "password": "secret123"},
            )
        assert resp.status_code == 200
        assert resp.json()["access_token"] == "login-tok"

    def test_wrong_credentials_returns_401(self):
        sb = _supabase_mock()
        sb.auth.sign_in_with_password.side_effect = Exception("Invalid credentials")

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/login/password",
                json={"email": "user@example.com", "password": "wrong"},
            )
        assert resp.status_code == 401

    def test_no_session_returns_401(self):
        sb = _supabase_mock()
        sb.auth.sign_in_with_password.return_value = _auth_response(session=None, user=MagicMock())

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/login/password",
                json={"email": "user@example.com", "password": "secret123"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/forgot-password
# ---------------------------------------------------------------------------

class TestForgotPassword:
    def test_always_returns_202(self):
        sb = _supabase_mock()
        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/forgot-password", json={"email": "user@example.com"}
            )
        assert resp.status_code == 202

    def test_returns_202_even_when_supabase_raises(self):
        """Enumeration defence: always 202 regardless of whether the address exists."""
        sb = _supabase_mock()
        sb.auth.reset_password_for_email.side_effect = Exception("unknown email")
        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/forgot-password", json={"email": "ghost@example.com"}
            )
        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# POST /auth/reset-password
# ---------------------------------------------------------------------------

class TestResetPassword:
    def test_short_password_returns_422(self):
        resp = TestClient(app).post(
            "/auth/reset-password",
            json={"access_token": "recovery-tok", "new_password": "short"},
        )
        assert resp.status_code == 422

    def test_success_returns_token(self):
        user = MagicMock()
        user.id = "uid-reset"
        session = MagicMock()
        session.access_token = "fresh-tok"

        sb = _supabase_mock()
        sb.auth.update_user.return_value = MagicMock(user=user)
        sb.auth.get_session.return_value = session

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/reset-password",
                json={"access_token": "recovery-tok", "new_password": "newpass123"},
            )
        assert resp.status_code == 200
        assert resp.json()["access_token"] == "fresh-tok"

    def test_supabase_error_returns_400(self):
        sb = _supabase_mock()
        sb.auth.set_session.side_effect = Exception("expired token")

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/reset-password",
                json={"access_token": "bad-tok", "new_password": "newpass123"},
            )
        assert resp.status_code == 400

    def test_no_user_after_update_returns_400(self):
        sb = _supabase_mock()
        sb.auth.update_user.return_value = MagicMock(user=None)

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/reset-password",
                json={"access_token": "recovery-tok", "new_password": "newpass123"},
            )
        assert resp.status_code == 400

    def test_no_session_after_update_returns_400(self):
        user = MagicMock()
        user.id = "uid-reset"
        sb = _supabase_mock()
        sb.auth.update_user.return_value = MagicMock(user=user)
        sb.auth.get_session.return_value = None   # session not established

        with patch("app.routers.auth.get_supabase", return_value=sb):
            resp = TestClient(app).post(
                "/auth/reset-password",
                json={"access_token": "recovery-tok", "new_password": "newpass123"},
            )
        assert resp.status_code == 400
