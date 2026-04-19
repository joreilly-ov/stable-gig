"""
Tests for the contractor_documents router.

Coverage
--------
POST /contractors/me/documents
  - 201 verified document returned when AI extracts required fields
  - 201 needs_review when AI returns all-null fields
  - 422 invalid document_type rejected
  - 403 non-contractor cannot upload
  - 422 propagated when document_verifier raises ValueError
  - 500 when DB insert returns no data
  - needs_review fallback when AI raises unexpected exception

GET /contractors/me/documents
  - 200 returns list for registered contractor
  - 403 non-contractor gets 403

GET /contractors/{contractor_id}/documents
  - 200 returns only verified non-expired docs
  - expired documents are filtered out
  - docs with no expiry are always included

DELETE /contractors/me/documents/{doc_id}
  - 204 soft-delete succeeds
  - 403 non-contractor cannot delete
  - 404 document not found

No real DB calls or Gemini calls — get_supabase_admin and document_verifier are patched.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.contractor_documents import router as docs_router
from app.dependencies import get_current_user

# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(docs_router)

_CONTRACTOR_USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_CONTRACTOR_ID      = "bbbbbbbb-0000-0000-0000-000000000002"
_OTHER_USER_ID      = "cccccccc-0000-0000-0000-000000000003"
_DOC_ID             = "dddddddd-0000-0000-0000-000000000004"

_CONTRACTOR_USER = SimpleNamespace(id=_CONTRACTOR_USER_ID)
_OTHER_USER      = SimpleNamespace(id=_OTHER_USER_ID)

_CONTRACTOR_ROW = {"id": _CONTRACTOR_ID}

_now_str     = datetime.now(timezone.utc).isoformat()
_future_str  = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat().replace("+00:00", "Z")
_past_str    = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")

_VERIFIED_DOC = {
    "id":                 _DOC_ID,
    "contractor_id":      _CONTRACTOR_ID,
    "document_type":      "insurance",
    "file_name":          "certificate.jpg",
    "file_source":        "data:image/jpeg;base64,/9j/abc",
    "status":             "verified",
    "extracted_data":     {
        "insured_name": "Acme Plumbing Ltd",
        "policy_number": "POL-1234",
        "expiry_date": "2027-06-30",
        "per_occurrence_limit": "£2,000,000",
        "insurer_name": "SafeGuard Insurance",
    },
    "verification_notes": None,
    "expires_at":         _future_str,
    "uploaded_at":        _now_str,
    "verified_at":        _now_str,
    "deleted_at":         None,
}

# Simulates what the DB returns for the public column-limited select
# (file_source is excluded from the GET /contractors/{id}/documents query).
_PUBLIC_DOC = {k: v for k, v in _VERIFIED_DOC.items() if k != "file_source"}

_NEEDS_REVIEW_DOC = {
    **_VERIFIED_DOC,
    "status":             "needs_review",
    "extracted_data":     {},
    "verification_notes": "Required fields could not be extracted.",
    "expires_at":         None,
    "verified_at":        None,
}

_UPLOAD_PAYLOAD = {
    "document_type": "insurance",
    "file_name":     "certificate.jpg",
    "file_source":   "data:image/jpeg;base64,/9j/abc",
}


# ---------------------------------------------------------------------------
# DB mock helper
# ---------------------------------------------------------------------------

def _make_db():
    db = MagicMock()
    db.table.return_value   = db
    db.select.return_value  = db
    db.insert.return_value  = db
    db.update.return_value  = db
    db.eq.return_value      = db
    db.is_.return_value     = db
    db.order.return_value   = db
    db.limit.return_value   = db
    db.execute.return_value = MagicMock(data=[])
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# POST /contractors/me/documents
# ---------------------------------------------------------------------------

class TestUploadDocument:
    def test_verified_document_201(self, contractor_client):
        from app.services.document_verifier import VerificationResult

        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # contractor lookup
            MagicMock(data=[_VERIFIED_DOC]),    # insert
        ]

        mock_result = VerificationResult(
            status="verified",
            extracted_data=_VERIFIED_DOC["extracted_data"],
            verification_notes=None,
            expires_at="2027-06-30",
        )

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db), \
             patch("app.services.document_verifier.verify_document", new=AsyncMock(return_value=mock_result)):
            resp = contractor_client.post("/contractors/me/documents", json=_UPLOAD_PAYLOAD)

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "verified"
        assert data["extracted_data"]["insured_name"] == "Acme Plumbing Ltd"

    def test_needs_review_201(self, contractor_client):
        from app.services.document_verifier import VerificationResult

        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),
            MagicMock(data=[_NEEDS_REVIEW_DOC]),
        ]

        mock_result = VerificationResult(
            status="needs_review",
            extracted_data={},
            verification_notes="Required fields could not be extracted.",
            expires_at=None,
        )

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db), \
             patch("app.services.document_verifier.verify_document", new=AsyncMock(return_value=mock_result)):
            resp = contractor_client.post("/contractors/me/documents", json=_UPLOAD_PAYLOAD)

        assert resp.status_code == 201
        assert resp.json()["status"] == "needs_review"

    def test_invalid_document_type_422(self, contractor_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[_CONTRACTOR_ROW])

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = contractor_client.post(
                "/contractors/me/documents",
                json={**_UPLOAD_PAYLOAD, "document_type": "passport"},
            )

        assert resp.status_code == 422
        assert "document_type" in resp.json()["detail"]

    def test_non_contractor_403(self, other_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[])  # no contractor row

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = other_client.post("/contractors/me/documents", json=_UPLOAD_PAYLOAD)

        assert resp.status_code == 403

    def test_verifier_value_error_422(self, contractor_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[_CONTRACTOR_ROW])

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db), \
             patch(
                 "app.services.document_verifier.verify_document",
                 new=AsyncMock(side_effect=ValueError("Malformed data URI")),
             ):
            resp = contractor_client.post("/contractors/me/documents", json=_UPLOAD_PAYLOAD)

        assert resp.status_code == 422
        assert "Malformed data URI" in resp.json()["detail"]

    def test_verifier_unexpected_exception_falls_back_to_needs_review(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),
            MagicMock(data=[_NEEDS_REVIEW_DOC]),
        ]

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db), \
             patch(
                 "app.services.document_verifier.verify_document",
                 new=AsyncMock(side_effect=RuntimeError("Network error")),
             ):
            resp = contractor_client.post("/contractors/me/documents", json=_UPLOAD_PAYLOAD)

        assert resp.status_code == 201
        assert resp.json()["status"] == "needs_review"

    def test_db_insert_failure_500(self, contractor_client):
        from app.services.document_verifier import VerificationResult

        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),
            MagicMock(data=[]),  # insert fails
        ]

        mock_result = VerificationResult(
            status="verified",
            extracted_data={"insured_name": "Acme", "expiry_date": "2027-01-01"},
            verification_notes=None,
            expires_at="2027-01-01",
        )

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db), \
             patch("app.services.document_verifier.verify_document", new=AsyncMock(return_value=mock_result)):
            resp = contractor_client.post("/contractors/me/documents", json=_UPLOAD_PAYLOAD)

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /contractors/me/documents
# ---------------------------------------------------------------------------

class TestListOwnDocuments:
    def test_returns_list_200(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # contractor lookup
            MagicMock(data=[_VERIFIED_DOC, _NEEDS_REVIEW_DOC]),  # document list
        ]

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = contractor_client.get("/contractors/me/documents")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_empty_list_200(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),
            MagicMock(data=[]),
        ]

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = contractor_client.get("/contractors/me/documents")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_non_contractor_403(self, other_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[])

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = other_client.get("/contractors/me/documents")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /contractors/{contractor_id}/documents  (public)
# ---------------------------------------------------------------------------

class TestListContractorDocuments:
    def test_returns_verified_non_expired(self, contractor_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[_PUBLIC_DOC])  # expires in the future

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = contractor_client.get(f"/contractors/{_CONTRACTOR_ID}/documents")

        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert "file_source" not in resp.json()[0]

    def test_expired_documents_filtered_out(self, contractor_client):
        expired_doc = {
            **_PUBLIC_DOC,
            "expires_at": _past_str,
        }
        db = _make_db()
        db.execute.return_value = MagicMock(data=[expired_doc])

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = contractor_client.get(f"/contractors/{_CONTRACTOR_ID}/documents")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_docs_with_no_expiry_always_included(self, contractor_client):
        no_expiry_doc = {
            **_PUBLIC_DOC,
            "expires_at": None,
        }
        db = _make_db()
        db.execute.return_value = MagicMock(data=[no_expiry_doc])

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = contractor_client.get(f"/contractors/{_CONTRACTOR_ID}/documents")

        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_empty_list_for_unknown_contractor(self, contractor_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[])

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = contractor_client.get(f"/contractors/unknown-id/documents")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# DELETE /contractors/me/documents/{doc_id}
# ---------------------------------------------------------------------------

class TestDeleteDocument:
    def test_soft_delete_204(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # contractor lookup
            MagicMock(data=[_VERIFIED_DOC]),    # document lookup
            MagicMock(data=[]),                 # update (soft delete)
        ]

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = contractor_client.delete(f"/contractors/me/documents/{_DOC_ID}")

        assert resp.status_code == 204

    def test_non_contractor_403(self, other_client):
        db = _make_db()
        db.execute.return_value = MagicMock(data=[])

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = other_client.delete(f"/contractors/me/documents/{_DOC_ID}")

        assert resp.status_code == 403

    def test_document_not_found_404(self, contractor_client):
        db = _make_db()
        db.execute.side_effect = [
            MagicMock(data=[_CONTRACTOR_ROW]),  # contractor found
            MagicMock(data=[]),                 # document not found
        ]

        with patch("app.routers.contractor_documents.get_supabase_admin", return_value=db):
            resp = contractor_client.delete(f"/contractors/me/documents/no-such-id")

        assert resp.status_code == 404
