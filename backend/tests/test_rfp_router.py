"""
Tests for the RFP generation router.

Coverage
--------
POST /jobs/{job_id}/rfp
  - 200 generates and returns RFP when analysis_result is present
  - 200 incorporates clarification answers in request body
  - 200 response shape: job_id, rfp_document, cost_estimate, permit_required, permit_notes
  - 200 stores rfp_document + cost fields back on the job row
  - 404 when job does not exist
  - 403 when caller is not the job owner
  - 422 when job has no analysis_result
  - 401 when unauthenticated
  - 502 when rfp_generator raises ValueError (bad AI response)
  - 503 when rfp_generator raises a generic exception (upstream failure)

No real DB or Gemini calls are made — everything is patched.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.rfp import router as rfp_router
from app.dependencies import get_current_user

# ---------------------------------------------------------------------------
# Test app setup
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(rfp_router)

_OWNER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_OTHER_ID  = "bbbbbbbb-0000-0000-0000-000000000002"

_OWNER_USER = SimpleNamespace(id=_OWNER_ID)
_OTHER_USER = SimpleNamespace(id=_OTHER_ID)

_JOB_ID = "cccccccc-0000-0000-0000-000000000003"

_ANALYSIS_RESULT = {
    "problem_type":       "plumbing",
    "description":        "Bathroom ceiling leak traced to a failed compression joint on the cold-water feed.",
    "location_in_home":   "bathroom",
    "urgency":            "high",
    "materials_involved": ["copper pipe", "compression joint"],
    "required_tools":     ["basin wrench", "PTFE tape"],
    "clarifying_questions": [
        "Is the leak active or intermittent?",
        "What is the approximate age of the pipework?",
    ],
}

_STUB_RFP_DOC = {
    "title":                "Bathroom Ceiling Leak — Plumbing Repair",
    "executive_summary":    "Repair of failed compression joint causing ceiling leak.",
    "scope_of_work":        "Replace compression joint on cold-water feed; dry-line ceiling if required.",
    "trade_category":       "plumbing",
    "urgency":              "high",
    "location_in_home":     "bathroom",
    "materials_noted":      ["copper pipe", "compression joint"],
    "special_requirements": "",
    "permit_required":      False,
    "permit_notes":         "",
    "cost_estimate": {
        "low_pence":  45000,
        "high_pence": 90000,
        "currency":   "GBP",
        "basis":      "Standard call-out + 2–3 hours labour",
    },
    "contractor_requirements": "Gas Safe not required; general plumbing competence sufficient",
    "bid_deadline_days":    5,
    "generated_at":         "2026-03-24T12:00:00+00:00",
}

_JOB_WITH_ANALYSIS = {
    "id":              _JOB_ID,
    "user_id":         _OWNER_ID,
    "title":           "Bathroom leak",
    "description":     "Ceiling is dripping",
    "activity":        "plumbing",
    "postcode":        "SW1A 1AA",
    "status":          "draft",
    "analysis_result": _ANALYSIS_RESULT,
    "rfp_document":    None,
}

_JOB_NO_ANALYSIS = {**_JOB_WITH_ANALYSIS, "analysis_result": None}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(job: dict | None = _JOB_WITH_ANALYSIS) -> MagicMock:
    db = MagicMock()
    job_chain = db.table.return_value.select.return_value.eq.return_value
    job_chain.execute.return_value.data = [job] if job else []
    db.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [job]
    return db


# ---------------------------------------------------------------------------
# Tests — happy path
# ---------------------------------------------------------------------------

def test_generate_rfp_returns_200():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.rfp.get_supabase_admin", return_value=db), \
         patch("app.routers.rfp.rfp_generator.generate", new=AsyncMock(return_value=_STUB_RFP_DOC)):
        client = TestClient(app)
        resp = client.post(f"/jobs/{_JOB_ID}/rfp", json={})

    assert resp.status_code == 200
    app.dependency_overrides.clear()


def test_generate_rfp_response_shape():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.rfp.get_supabase_admin", return_value=db), \
         patch("app.routers.rfp.rfp_generator.generate", new=AsyncMock(return_value=_STUB_RFP_DOC)):
        client = TestClient(app)
        data = client.post(f"/jobs/{_JOB_ID}/rfp", json={}).json()

    assert data["job_id"] == _JOB_ID
    assert data["rfp_document"]["title"] == _STUB_RFP_DOC["title"]
    assert data["cost_estimate"]["low_pence"]  == 45000
    assert data["cost_estimate"]["high_pence"] == 90000
    assert data["cost_estimate"]["currency"]   == "GBP"
    assert data["permit_required"] is False
    assert isinstance(data["permit_notes"], str)
    app.dependency_overrides.clear()


def test_generate_rfp_passes_clarifications_to_service():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()
    mock_generate = AsyncMock(return_value=_STUB_RFP_DOC)
    answers = {"Is the leak active or intermittent?": "Active — drips when water is running"}

    with patch("app.routers.rfp.get_supabase_admin", return_value=db), \
         patch("app.routers.rfp.rfp_generator.generate", new=mock_generate):
        client = TestClient(app)
        client.post(f"/jobs/{_JOB_ID}/rfp", json={"clarification_answers": answers})

    call_kwargs = mock_generate.call_args.kwargs
    assert call_kwargs["clarification_answers"] == answers
    app.dependency_overrides.clear()


def test_generate_rfp_stores_fields_on_job():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.rfp.get_supabase_admin", return_value=db), \
         patch("app.routers.rfp.rfp_generator.generate", new=AsyncMock(return_value=_STUB_RFP_DOC)):
        client = TestClient(app)
        client.post(f"/jobs/{_JOB_ID}/rfp", json={})

    update_call = db.table.return_value.update.call_args
    payload = update_call.args[0]
    assert "rfp_document"            in payload
    assert "cost_estimate_low_pence"  in payload
    assert "cost_estimate_high_pence" in payload
    assert "permit_required"          in payload
    app.dependency_overrides.clear()


def test_generate_rfp_with_permit_required():
    rfp_with_permit = {**_STUB_RFP_DOC, "permit_required": True, "permit_notes": "Structural work requires building regs approval"}
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.rfp.get_supabase_admin", return_value=db), \
         patch("app.routers.rfp.rfp_generator.generate", new=AsyncMock(return_value=rfp_with_permit)):
        client = TestClient(app)
        data = client.post(f"/jobs/{_JOB_ID}/rfp", json={}).json()

    assert data["permit_required"] is True
    assert "building regs" in data["permit_notes"]
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests — auth errors
# ---------------------------------------------------------------------------

def test_generate_rfp_requires_auth():
    client = TestClient(app)
    resp = client.post(f"/jobs/{_JOB_ID}/rfp", json={})
    assert resp.status_code == 401


def test_generate_rfp_403_for_non_owner():
    app.dependency_overrides[get_current_user] = lambda: _OTHER_USER
    db = _make_db()

    with patch("app.routers.rfp.get_supabase_admin", return_value=db), \
         patch("app.routers.rfp.rfp_generator.generate", new=AsyncMock(return_value=_STUB_RFP_DOC)):
        client = TestClient(app)
        resp = client.post(f"/jobs/{_JOB_ID}/rfp", json={})

    assert resp.status_code == 403
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests — not-found / validation errors
# ---------------------------------------------------------------------------

def test_generate_rfp_404_unknown_job():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db(job=None)

    with patch("app.routers.rfp.get_supabase_admin", return_value=db):
        client = TestClient(app)
        resp = client.post(f"/jobs/{_JOB_ID}/rfp", json={})

    assert resp.status_code == 404
    app.dependency_overrides.clear()


def test_generate_rfp_422_no_analysis_result():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db(job=_JOB_NO_ANALYSIS)

    with patch("app.routers.rfp.get_supabase_admin", return_value=db):
        client = TestClient(app)
        resp = client.post(f"/jobs/{_JOB_ID}/rfp", json={})

    assert resp.status_code == 422
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests — upstream failures
# ---------------------------------------------------------------------------

def test_generate_rfp_502_on_parse_error():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.rfp.get_supabase_admin", return_value=db), \
         patch("app.routers.rfp.rfp_generator.generate", side_effect=ValueError("bad JSON")):
        client = TestClient(app)
        resp = client.post(f"/jobs/{_JOB_ID}/rfp", json={})

    assert resp.status_code == 502
    app.dependency_overrides.clear()


def test_generate_rfp_503_on_api_failure():
    app.dependency_overrides[get_current_user] = lambda: _OWNER_USER
    db = _make_db()

    with patch("app.routers.rfp.get_supabase_admin", return_value=db), \
         patch("app.routers.rfp.rfp_generator.generate", side_effect=Exception("network timeout")):
        client = TestClient(app)
        resp = client.post(f"/jobs/{_JOB_ID}/rfp", json={})

    assert resp.status_code == 503
    app.dependency_overrides.clear()
