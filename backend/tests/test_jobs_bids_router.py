"""
Tests for the jobs and bids routers.

Coverage
--------
Jobs:
  POST /jobs
    - 201 with full payload including analysis_result
    - 201 without optional analysis_result
    - 422 when activity is invalid
    - 422 when title too short
    - 401 when unauthenticated
    - 500 when DB insert returns no data

  GET /jobs
    - homeowner sees own jobs (all statuses)
    - contractor sees only 'open' jobs

  GET /jobs/{id}
    - owner sees any status
    - contractor can see open job
    - contractor cannot see draft job
    - 404 for unknown job

  PATCH /jobs/{id}
    - owner updates title
    - owner transitions draft → open
    - 422 for invalid transition (open → draft not allowed)
    - 403 when non-owner tries to update

Bids:
  POST /jobs/{job_id}/bids
    - 201 when contractor bids on open job
    - 422 when job is not open (draft)
    - 403 when non-contractor bids
    - 409 when contractor bids twice

  GET /jobs/{job_id}/bids
    - owner sees all bids
    - contractor sees only their own bid
    - non-owner non-contractor gets 403

  PATCH /jobs/{job_id}/bids/{bid_id}
    - accept: bid → accepted, others → rejected, job → awarded
    - reject: bid → rejected
    - 422 when job not open
    - 403 when non-owner tries

  GET /me/bids
    - contractor sees their bids with job info
    - non-contractor gets 403

No real DB calls are made — get_supabase_admin is patched throughout.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.jobs import router as jobs_router
from app.routers.bids import router as bids_router
from app.dependencies import get_current_user

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(jobs_router)
app.include_router(bids_router)

# Shared stub user identities
_OWNER_ID      = "aaaaaaaa-0000-0000-0000-000000000001"
_CONTRACTOR_USER_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_CONTRACTOR_ID      = "cccccccc-0000-0000-0000-000000000003"
_OTHER_USER_ID      = "dddddddd-0000-0000-0000-000000000004"

_OWNER_USER      = SimpleNamespace(id=_OWNER_ID)
_CONTRACTOR_USER = SimpleNamespace(id=_CONTRACTOR_USER_ID)
_OTHER_USER      = SimpleNamespace(id=_OTHER_USER_ID)

_OPEN_JOB = {
    "id":          "job-0001",
    "user_id":     _OWNER_ID,
    "title":       "Leaky tap",
    "description": "The kitchen tap drips constantly.",
    "activity":    "plumbing",
    "postcode":    "SW1A 1AA",
    "status":      "open",
    "created_at":  "2026-03-21T10:00:00Z",
    "analysis_result": None,
}

_DRAFT_JOB = {**_OPEN_JOB, "id": "job-0002", "status": "draft"}

_PENDING_BID = {
    "id":            "bid-0001",
    "job_id":        "job-0001",
    "contractor_id": _CONTRACTOR_ID,
    "amount_pence":  15000,
    "note":          "I will replace the cartridge and reseal.",
    "status":        "pending",
    "created_at":    "2026-03-21T11:00:00Z",
}

_CONTRACTOR_ROW = {"id": _CONTRACTOR_ID}


# ---------------------------------------------------------------------------
# Helpers to build supabase mock chains
# ---------------------------------------------------------------------------

def _make_db_mock():
    """Return a MagicMock that mimics supabase client's chained query API."""
    db = MagicMock()
    # Each table() call returns a fresh MagicMock with chainable methods
    db.table.return_value = db
    db.select.return_value = db
    db.insert.return_value = db
    db.update.return_value = db
    db.eq.return_value = db
    db.neq.return_value = db
    db.order.return_value = db
    db.limit.return_value = db
    db.execute.return_value = MagicMock(data=[])
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def owner_client():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def contractor_client():
    app.dependency_overrides[get_current_user] = lambda: _CONTRACTOR_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def other_client():
    app.dependency_overrides[get_current_user] = lambda: _OTHER_USER
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def anon_client():
    # No override → get_current_user will 401 (real dependency, no token)
    yield TestClient(app)


# ---------------------------------------------------------------------------
# POST /jobs
# ---------------------------------------------------------------------------

class TestCreateJob:
    def test_creates_job_201(self, owner_client):
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[{**_OPEN_JOB, "status": "draft"}])

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.post("/jobs", json={
                "title":       "Leaky tap",
                "description": "The kitchen tap drips constantly.",
                "activity":    "plumbing",
                "postcode":    "SW1A 1AA",
            })

        assert resp.status_code == 201
        assert resp.json()["status"] == "draft"

    def test_creates_job_with_analysis_result(self, owner_client):
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[{**_OPEN_JOB, "status": "draft", "analysis_result": {"urgency": 7}}])

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.post("/jobs", json={
                "title":           "Roof leak",
                "description":     "Water coming through the ceiling after rain.",
                "activity":        "roofing",
                "postcode":        "EC1A 1BB",
                "analysis_result": {"urgency": 7},
            })

        assert resp.status_code == 201

    def test_invalid_activity_422(self, owner_client):
        db = _make_db_mock()
        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.post("/jobs", json={
                "title":       "Fix something",
                "description": "Something needs fixing urgently.",
                "activity":    "blacksmithing",
                "postcode":    "W1A 1AA",
            })
        assert resp.status_code == 422

    def test_title_too_short_422(self, owner_client):
        db = _make_db_mock()
        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.post("/jobs", json={
                "title":       "Hi",
                "description": "Something needs fixing urgently.",
                "activity":    "plumbing",
                "postcode":    "W1A 1AA",
            })
        assert resp.status_code == 422

    def test_unauthenticated_401(self, anon_client):
        resp = anon_client.post("/jobs", json={
            "title":       "Leaky tap",
            "description": "The kitchen tap drips constantly.",
            "activity":    "plumbing",
            "postcode":    "SW1A 1AA",
        })
        assert resp.status_code == 401

    def test_db_failure_500(self, owner_client):
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[])  # empty → failure

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.post("/jobs", json={
                "title":       "Leaky tap",
                "description": "The kitchen tap drips constantly.",
                "activity":    "plumbing",
                "postcode":    "SW1A 1AA",
            })
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /jobs
# ---------------------------------------------------------------------------

class TestListJobs:
    def test_homeowner_sees_own_jobs(self, owner_client):
        db = _make_db_mock()
        # _is_contractor check returns empty → not a contractor
        # list_jobs query returns their jobs
        db.execute.side_effect = [
            MagicMock(data=[]),          # contractors check
            MagicMock(data=[_OPEN_JOB, _DRAFT_JOB]),  # own jobs
        ]

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.get("/jobs")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_contractor_sees_open_jobs(self, contractor_client):
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # contractors check → is contractor
            MagicMock(data=[_OPEN_JOB]),         # open jobs
        ]

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = contractor_client.get("/jobs")

        assert resp.status_code == 200
        assert all(j["status"] == "open" for j in resp.json())


# ---------------------------------------------------------------------------
# GET /jobs/{id}
# ---------------------------------------------------------------------------

class TestGetJob:
    def test_owner_sees_own_draft(self, owner_client):
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[_DRAFT_JOB])

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.get(f"/jobs/{_DRAFT_JOB['id']}")

        assert resp.status_code == 200

    def test_contractor_sees_open_job(self, contractor_client):
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),       # job lookup
            MagicMock(data=[_CONTRACTOR_ROW]), # _is_contractor
        ]

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = contractor_client.get(f"/jobs/{_OPEN_JOB['id']}")

        assert resp.status_code == 200

    def test_contractor_cannot_see_draft(self, contractor_client):
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_DRAFT_JOB]),      # job lookup
            MagicMock(data=[_CONTRACTOR_ROW]), # _is_contractor
        ]

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = contractor_client.get(f"/jobs/{_DRAFT_JOB['id']}")

        assert resp.status_code == 403

    def test_404_for_unknown_job(self, owner_client):
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[])

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.get("/jobs/does-not-exist")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /jobs/{id}
# ---------------------------------------------------------------------------

class TestUpdateJob:
    def test_owner_updates_title(self, owner_client):
        updated = {**_DRAFT_JOB, "title": "Updated title"}
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_DRAFT_JOB]),  # get_job_or_404
            MagicMock(data=[updated]),     # update
        ]

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.patch(f"/jobs/{_DRAFT_JOB['id']}", json={"title": "Updated title"})

        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated title"

    def test_owner_transitions_draft_to_open(self, owner_client):
        opened = {**_DRAFT_JOB, "status": "open"}
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_DRAFT_JOB]),  # get_job_or_404
            MagicMock(data=[opened]),      # update
        ]

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.patch(f"/jobs/{_DRAFT_JOB['id']}", json={"status": "open"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "open"

    def test_invalid_transition_422(self, owner_client):
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[_OPEN_JOB])  # open job

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = owner_client.patch(f"/jobs/{_OPEN_JOB['id']}", json={"status": "draft"})

        assert resp.status_code == 422

    def test_non_owner_403(self, other_client):
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[_OPEN_JOB])

        with patch("app.routers.jobs.get_supabase_admin", return_value=db):
            resp = other_client.patch(f"/jobs/{_OPEN_JOB['id']}", json={"title": "Hijacked"})

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/bids
# ---------------------------------------------------------------------------

class TestPlaceBid:
    _VALID_BID = {"amount_pence": 15000, "note": "I will replace the cartridge and reseal completely."}

    def test_contractor_bids_on_open_job(self, contractor_client):
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # _get_contractor_or_403
            MagicMock(data=[_OPEN_JOB]),        # _get_job_or_404
            MagicMock(data=[]),                 # existing bid check
            MagicMock(data=[_PENDING_BID]),     # insert
        ]

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = contractor_client.post(f"/jobs/{_OPEN_JOB['id']}/bids", json=self._VALID_BID)

        assert resp.status_code == 201
        assert resp.json()["status"] == "pending"

    def test_bid_on_non_open_job_422(self, contractor_client):
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # contractor check
            MagicMock(data=[_DRAFT_JOB]),       # job is draft
        ]

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = contractor_client.post(f"/jobs/{_DRAFT_JOB['id']}/bids", json=self._VALID_BID)

        assert resp.status_code == 422

    def test_non_contractor_403(self, owner_client):
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[])  # not a contractor

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = owner_client.post(f"/jobs/{_OPEN_JOB['id']}/bids", json=self._VALID_BID)

        assert resp.status_code == 403

    def test_duplicate_bid_409(self, contractor_client):
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),   # contractor check
            MagicMock(data=[_OPEN_JOB]),         # job lookup
            MagicMock(data=[_PENDING_BID]),      # existing bid found → 409
        ]

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = contractor_client.post(f"/jobs/{_OPEN_JOB['id']}/bids", json=self._VALID_BID)

        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/bids
# ---------------------------------------------------------------------------

class TestListBids:
    def test_owner_sees_all_bids(self, owner_client):
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),     # job lookup
            MagicMock(data=[_PENDING_BID]),  # all bids
        ]

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = owner_client.get(f"/jobs/{_OPEN_JOB['id']}/bids")

        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_contractor_sees_own_bid(self, contractor_client):
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),       # job lookup
            MagicMock(data=[_CONTRACTOR_ROW]), # contractor lookup
            MagicMock(data=[_PENDING_BID]),    # own bid
        ]

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = contractor_client.get(f"/jobs/{_OPEN_JOB['id']}/bids")

        assert resp.status_code == 200

    def test_non_owner_non_contractor_403(self, other_client):
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),  # job lookup — not owner
            MagicMock(data=[]),           # not a contractor
        ]

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = other_client.get(f"/jobs/{_OPEN_JOB['id']}/bids")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PATCH /jobs/{job_id}/bids/{bid_id}
# ---------------------------------------------------------------------------

class TestActionBid:
    def test_owner_accepts_bid(self, owner_client):
        accepted_bid = {**_PENDING_BID, "status": "accepted"}
        awarded_job  = {**_OPEN_JOB,   "status": "awarded"}
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),     # job lookup
            MagicMock(data=[_PENDING_BID]),  # bid lookup
            MagicMock(data=[accepted_bid]),  # accept this bid
            MagicMock(data=[]),              # reject others
            MagicMock(data=[awarded_job]),   # award job
            MagicMock(data=[accepted_bid]),  # final bid fetch
        ]

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_OPEN_JOB['id']}/bids/{_PENDING_BID['id']}",
                json={"action": "accept"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["bid"]["status"] == "accepted"
        assert data["job"]["status"] == "awarded"

    def test_owner_rejects_bid(self, owner_client):
        rejected_bid = {**_PENDING_BID, "status": "rejected"}
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_OPEN_JOB]),     # job lookup
            MagicMock(data=[_PENDING_BID]),  # bid lookup
            MagicMock(data=[rejected_bid]),  # update
        ]

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_OPEN_JOB['id']}/bids/{_PENDING_BID['id']}",
                json={"action": "reject"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_invalid_action_422(self, owner_client):
        db = _make_db_mock()
        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_OPEN_JOB['id']}/bids/{_PENDING_BID['id']}",
                json={"action": "maybe"},
            )
        assert resp.status_code == 422

    def test_non_owner_cannot_accept_403(self, other_client):
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[_OPEN_JOB])

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = other_client.patch(
                f"/jobs/{_OPEN_JOB['id']}/bids/{_PENDING_BID['id']}",
                json={"action": "accept"},
            )

        assert resp.status_code == 403

    def test_cannot_action_on_non_open_job_422(self, owner_client):
        awarded_job = {**_OPEN_JOB, "status": "awarded"}
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[awarded_job])

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = owner_client.patch(
                f"/jobs/{_OPEN_JOB['id']}/bids/{_PENDING_BID['id']}",
                json={"action": "accept"},
            )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /me/bids
# ---------------------------------------------------------------------------

class TestMyBids:
    def test_contractor_sees_own_bids(self, contractor_client):
        bid_with_job = {**_PENDING_BID, "jobs": _OPEN_JOB}
        db = _make_db_mock()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # contractor lookup
            MagicMock(data=[bid_with_job]),     # bids with job info
        ]

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = contractor_client.get("/me/bids")

        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_non_contractor_403(self, owner_client):
        db = _make_db_mock()
        db.execute.return_value = MagicMock(data=[])  # not a contractor

        with patch("app.routers.bids.get_supabase_admin", return_value=db):
            resp = owner_client.get("/me/bids")

        assert resp.status_code == 403
