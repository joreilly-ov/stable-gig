"""
Tests for the contractor matching router.

Coverage
--------
GET /jobs/{job_id}/contractors/matches
  - 200 with embedding-based matches (strategy = "embedding")
  - 200 with activity-fallback matches (strategy = "activity_fallback")
  - 200 respects the limit query parameter
  - 422 when limit is out of range
  - 403 when caller is not the job owner
  - 404 when job does not exist
  - 401 when unauthenticated
  - 503 when the matching service raises an exception

POST /me/contractor/embed-profile
  - 200 returns profile_text and embedding_dimensions
  - 403 when caller is not a registered contractor
  - 422 when profile has insufficient data (ValueError from service)
  - 503 when the embedding service raises a generic exception
  - 401 when unauthenticated

No real DB or Gemini calls are made — everything is patched.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.contractor_matching import router as matching_router
from app.dependencies import get_current_user

# ---------------------------------------------------------------------------
# Test app setup
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(matching_router)

_OWNER_ID      = "aaaaaaaa-0000-0000-0000-000000000001"
_CONTRACTOR_UID = "bbbbbbbb-0000-0000-0000-000000000002"
_CONTRACTOR_ID  = "cccccccc-0000-0000-0000-000000000003"
_OTHER_ID       = "dddddddd-0000-0000-0000-000000000004"
_JOB_ID         = "eeeeeeee-0000-0000-0000-000000000005"

_OWNER_USER      = SimpleNamespace(id=_OWNER_ID)
_CONTRACTOR_USER = SimpleNamespace(id=_CONTRACTOR_UID)
_OTHER_USER      = SimpleNamespace(id=_OTHER_ID)

_JOB = {
    "id":           _JOB_ID,
    "user_id":      _OWNER_ID,
    "title":        "Bathroom leak",
    "description":  "Ceiling is dripping after rain",
    "activity":     "plumbing",
    "postcode":     "SW1A 1AA",
    "status":       "open",
    "rfp_document": {
        "scope_of_work":         "Replace failed compression joint on cold-water feed",
        "executive_summary":     "Plumbing repair to ceiling leak",
        "contractor_requirements": "General plumbing competence",
    },
}

_CONTRACTOR_ROWS_EMBEDDING = [
    {
        "id":            _CONTRACTOR_ID,
        "user_id":       _CONTRACTOR_UID,
        "business_name": "London Plumbing Co",
        "activities":    ["plumbing"],
        "postcode":      "SW1B 2BB",
        "match_score":   0.92,
        "contractor_details": {"years_experience": 10, "insurance_verified": True},
    },
]

_CONTRACTOR_ROWS_FALLBACK = [
    {**_CONTRACTOR_ROWS_EMBEDDING[0], "match_score": None},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(job: dict | None = _JOB, contractor_id: str | None = None) -> MagicMock:
    """Build a mock Supabase admin client for the matching router."""
    db = MagicMock()

    # jobs query (called by _get_job_or_404)
    job_chain = db.table.return_value.select.return_value.eq.return_value
    job_chain.execute.return_value.data = [job] if job else []

    # contractors query (called by _get_contractor_id_or_403)
    if contractor_id:
        db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"id": contractor_id}
        ]
    else:
        db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

    return db


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/contractors/matches — happy paths
# ---------------------------------------------------------------------------

def test_match_contractors_embedding_strategy():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch(
             "app.routers.contractor_matching.contractor_matcher.find_matching_contractors",
             new=AsyncMock(return_value=_CONTRACTOR_ROWS_EMBEDDING),
         ):
        client = TestClient(app)
        resp = client.get(f"/jobs/{_JOB_ID}/contractors/matches")

    assert resp.status_code == 200
    data = resp.json()
    assert data["strategy"] == "embedding"
    assert len(data["matches"]) == 1
    assert data["matches"][0]["match_score"] == pytest.approx(0.92)
    app.dependency_overrides.clear()


def test_match_contractors_activity_fallback_strategy():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch(
             "app.routers.contractor_matching.contractor_matcher.find_matching_contractors",
             new=AsyncMock(return_value=_CONTRACTOR_ROWS_FALLBACK),
         ):
        client = TestClient(app)
        resp = client.get(f"/jobs/{_JOB_ID}/contractors/matches")

    assert resp.status_code == 200
    data = resp.json()
    assert data["strategy"] == "activity_fallback"
    assert data["matches"][0]["match_score"] is None
    app.dependency_overrides.clear()


def test_match_contractors_empty_result():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch(
             "app.routers.contractor_matching.contractor_matcher.find_matching_contractors",
             new=AsyncMock(return_value=[]),
         ):
        client = TestClient(app)
        resp = client.get(f"/jobs/{_JOB_ID}/contractors/matches")

    assert resp.status_code == 200
    assert resp.json()["matches"] == []
    app.dependency_overrides.clear()


def test_match_contractors_passes_limit_to_service():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()
    mock_find = AsyncMock(return_value=[])

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_matching.contractor_matcher.find_matching_contractors", new=mock_find):
        client = TestClient(app)
        client.get(f"/jobs/{_JOB_ID}/contractors/matches?limit=5")

    call_kwargs = mock_find.call_args.kwargs
    assert call_kwargs["limit"] == 5
    app.dependency_overrides.clear()


def test_match_contractors_returns_job_id_in_response():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch(
             "app.routers.contractor_matching.contractor_matcher.find_matching_contractors",
             new=AsyncMock(return_value=[]),
         ):
        client = TestClient(app)
        data = client.get(f"/jobs/{_JOB_ID}/contractors/matches").json()

    assert data["job_id"] == _JOB_ID
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET — validation / auth errors
# ---------------------------------------------------------------------------

def test_match_contractors_422_limit_too_large():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch(
             "app.routers.contractor_matching.contractor_matcher.find_matching_contractors",
             new=AsyncMock(return_value=[]),
         ):
        client = TestClient(app)
        resp = client.get(f"/jobs/{_JOB_ID}/contractors/matches?limit=100")

    assert resp.status_code == 422
    app.dependency_overrides.clear()


def test_match_contractors_403_non_owner():
    app.dependency_overrides[get_current_user] = lambda: _OTHER_USER
    db = _make_db()

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db):
        client = TestClient(app)
        resp = client.get(f"/jobs/{_JOB_ID}/contractors/matches")

    assert resp.status_code == 403
    app.dependency_overrides.clear()


def test_match_contractors_404_unknown_job():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db(job=None)

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db):
        client = TestClient(app)
        resp = client.get(f"/jobs/{_JOB_ID}/contractors/matches")

    assert resp.status_code == 404
    app.dependency_overrides.clear()


def test_match_contractors_requires_auth():
    client = TestClient(app)
    resp = client.get(f"/jobs/{_JOB_ID}/contractors/matches")
    assert resp.status_code == 401


def test_match_contractors_503_on_service_error():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch(
             "app.routers.contractor_matching.contractor_matcher.find_matching_contractors",
             side_effect=Exception("connection refused"),
         ):
        client = TestClient(app)
        resp = client.get(f"/jobs/{_JOB_ID}/contractors/matches")

    assert resp.status_code == 503
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /me/contractor/embed-profile — happy path
# ---------------------------------------------------------------------------

def test_embed_profile_200():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(contractor_id=_CONTRACTOR_ID)

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch(
             "app.routers.contractor_matching.contractor_matcher.update_contractor_embedding",
             new=AsyncMock(return_value={"profile_text": "London Plumbing Co. Trades: plumbing.", "embedding_dimensions": 768}),
         ):
        client = TestClient(app)
        resp = client.post("/me/contractor/embed-profile")

    assert resp.status_code == 200
    data = resp.json()
    assert data["embedding_dimensions"] == 768
    assert "profile_text" in data
    app.dependency_overrides.clear()


def test_embed_profile_passes_contractor_id_to_service():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(contractor_id=_CONTRACTOR_ID)
    mock_update = AsyncMock(return_value={"profile_text": "t", "embedding_dimensions": 768})

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch("app.routers.contractor_matching.contractor_matcher.update_contractor_embedding", new=mock_update):
        client = TestClient(app)
        client.post("/me/contractor/embed-profile")

    assert mock_update.call_args.args[0] == _CONTRACTOR_ID
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /me/contractor/embed-profile — errors
# ---------------------------------------------------------------------------

def test_embed_profile_403_not_a_contractor():
    app.dependency_overrides[get_current_user] = lambda: _OTHER_USER
    db = _make_db(contractor_id=None)

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db):
        client = TestClient(app)
        resp = client.post("/me/contractor/embed-profile")

    assert resp.status_code == 403
    app.dependency_overrides.clear()


def test_embed_profile_422_insufficient_data():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(contractor_id=_CONTRACTOR_ID)

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch(
             "app.routers.contractor_matching.contractor_matcher.update_contractor_embedding",
             side_effect=ValueError("no activities set"),
         ):
        client = TestClient(app)
        resp = client.post("/me/contractor/embed-profile")

    assert resp.status_code == 422
    app.dependency_overrides.clear()


def test_embed_profile_503_on_api_failure():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    db = _make_db(contractor_id=_CONTRACTOR_ID)

    with patch("app.routers.contractor_matching.get_supabase_admin", return_value=db), \
         patch(
             "app.routers.contractor_matching.contractor_matcher.update_contractor_embedding",
             side_effect=Exception("Gemini API down"),
         ):
        client = TestClient(app)
        resp = client.post("/me/contractor/embed-profile")

    assert resp.status_code == 503
    app.dependency_overrides.clear()


def test_embed_profile_requires_auth():
    client = TestClient(app)
    resp = client.post("/me/contractor/embed-profile")
    assert resp.status_code == 401
