"""Integration tests for the profiles router (GET/PATCH /me/profile).

Coverage
--------
GET /me/profile
  - 401 when no auth token provided
  - 404 when Supabase returns no data
  - 200 with profile data on success

PATCH /me/profile
  - 401 when no auth token provided
  - 422 when body is empty (no fields)
  - 404 when Supabase returns no data after update
  - 200 with updated profile on success
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user
from app.routers.profiles import router

app = FastAPI()
app.include_router(router)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROFILE = {
    "id": "uid-1",
    "full_name": "Alice Smith",
    "postcode": "90210",
    "road_address": "123 Main St",
    "city": "Beverly Hills",
    "state": "CA",
    "created_at": "2024-01-01T00:00:00",
}


def _mock_user(uid="uid-1"):
    u = MagicMock()
    u.id = uid
    return u


@pytest.fixture()
def authed_client():
    user = _mock_user()
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def unauthed_client():
    # No override → real dependency → raises 401 (since there's no bearer token)
    app.dependency_overrides.clear()
    yield TestClient(app)


# ---------------------------------------------------------------------------
# GET /me/profile
# ---------------------------------------------------------------------------

class TestGetProfile:
    def test_unauthenticated_returns_401(self, unauthed_client):
        resp = unauthed_client.get("/me/profile")
        assert resp.status_code == 401

    def test_not_found_returns_404(self, authed_client):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = None

        with patch("app.routers.profiles.get_supabase", return_value=sb):
            resp = authed_client.get("/me/profile")
        assert resp.status_code == 404

    def test_success_returns_profile(self, authed_client):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = _PROFILE

        with patch("app.routers.profiles.get_supabase", return_value=sb):
            resp = authed_client.get("/me/profile")
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Alice Smith"
        assert resp.json()["city"] == "Beverly Hills"


# ---------------------------------------------------------------------------
# PATCH /me/profile
# ---------------------------------------------------------------------------

class TestUpdateProfile:
    def test_unauthenticated_returns_401(self, unauthed_client):
        resp = unauthed_client.patch("/me/profile", json={"full_name": "Bob"})
        assert resp.status_code == 401

    def test_empty_body_returns_422(self, authed_client):
        resp = authed_client.patch("/me/profile", json={})
        assert resp.status_code == 422

    def test_not_found_after_update_returns_404(self, authed_client):
        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []

        with patch("app.routers.profiles.get_supabase", return_value=sb):
            resp = authed_client.patch("/me/profile", json={"full_name": "Bob"})
        assert resp.status_code == 404

    def test_success_returns_updated_profile(self, authed_client):
        updated = {**_PROFILE, "full_name": "Bob Jones"}
        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [updated]

        with patch("app.routers.profiles.get_supabase", return_value=sb):
            resp = authed_client.patch("/me/profile", json={"full_name": "Bob Jones"})
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Bob Jones"

    def test_partial_update_only_sends_provided_fields(self, authed_client):
        updated = {**_PROFILE, "city": "Malibu"}
        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [updated]

        with patch("app.routers.profiles.get_supabase", return_value=sb):
            resp = authed_client.patch("/me/profile", json={"city": "Malibu"})

        # Verify only the city was sent in the update call
        update_data = sb.table.return_value.update.call_args[0][0]
        assert "city" in update_data
        assert "full_name" not in update_data
