"""Integration tests for the user_metadata router (GET/PATCH /me/metadata).

Coverage
--------
GET /me/metadata
  - 401 when unauthenticated
  - 404 when Supabase returns no data
  - 200 with metadata on success

PATCH /me/metadata
  - 401 when unauthenticated
  - 422 when body is empty
  - 422 when trade_interests contains invalid category
  - 200 with updated metadata on success
  - 500 when re-fetch after upsert returns no data
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user
from app.routers.user_metadata import router

app = FastAPI()
app.include_router(router)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_METADATA = {
    "id": "uid-1",
    "username": "alice99",
    "bio": "Loves home improvement",
    "trade_interests": ["plumbing", "electrical"],
    "setup_complete": True,
    "updated_at": "2024-06-01T12:00:00",
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
    app.dependency_overrides.clear()
    yield TestClient(app)


# ---------------------------------------------------------------------------
# GET /me/metadata
# ---------------------------------------------------------------------------

class TestGetMetadata:
    def test_unauthenticated_returns_401(self, unauthed_client):
        resp = unauthed_client.get("/me/metadata")
        assert resp.status_code == 401

    def test_not_found_returns_404(self, authed_client):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = None

        with patch("app.routers.user_metadata.get_supabase", return_value=sb):
            resp = authed_client.get("/me/metadata")
        assert resp.status_code == 404

    def test_success_returns_metadata(self, authed_client):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = _METADATA

        with patch("app.routers.user_metadata.get_supabase", return_value=sb):
            resp = authed_client.get("/me/metadata")
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "alice99"
        assert body["trade_interests"] == ["plumbing", "electrical"]


# ---------------------------------------------------------------------------
# PATCH /me/metadata
# ---------------------------------------------------------------------------

class TestUpdateMetadata:
    def test_unauthenticated_returns_401(self, unauthed_client):
        resp = unauthed_client.patch("/me/metadata", json={"username": "bob"})
        assert resp.status_code == 401

    def test_empty_body_returns_422(self, authed_client):
        resp = authed_client.patch("/me/metadata", json={})
        assert resp.status_code == 422

    def test_invalid_trade_interest_returns_422(self, authed_client):
        resp = authed_client.patch(
            "/me/metadata",
            json={"trade_interests": ["plumbing", "carpentry"]},  # carpentry not valid
        )
        assert resp.status_code == 422

    def test_success_returns_updated_metadata(self, authed_client):
        updated = {**_METADATA, "username": "bob42"}
        sb = MagicMock()
        # upsert call
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        # re-fetch call
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = updated

        with patch("app.routers.user_metadata.get_supabase", return_value=sb):
            resp = authed_client.patch("/me/metadata", json={"username": "bob42"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "bob42"

    def test_refetch_failure_returns_500(self, authed_client):
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = None

        with patch("app.routers.user_metadata.get_supabase", return_value=sb):
            resp = authed_client.patch("/me/metadata", json={"username": "bob42"})
        assert resp.status_code == 500

    def test_valid_trade_interests_accepted(self, authed_client):
        updated = {**_METADATA, "trade_interests": ["roofing", "damp"]}
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = updated

        with patch("app.routers.user_metadata.get_supabase", return_value=sb):
            resp = authed_client.patch(
                "/me/metadata", json={"trade_interests": ["roofing", "damp"]}
            )
        assert resp.status_code == 200
