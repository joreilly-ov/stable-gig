"""Contractor document upload and verification.

POST   /contractors/me/documents                  — upload & auto-verify a document
GET    /contractors/me/documents                  — list own documents (all statuses)
GET    /contractors/{contractor_id}/documents     — list a contractor's verified documents (public)
DELETE /contractors/me/documents/{doc_id}         — soft-delete own document

Document lifecycle
------------------
  upload → AI verification runs immediately on the image
  status = 'verified'     if required fields were successfully extracted
  status = 'needs_review' if required fields are all null (bad scan, wrong doc, etc.)
  status = 'rejected'     set manually by admins via the service-role client

Auth: upload / list-own / delete require a valid Supabase JWT (contractor only).
      Public listing (/contractors/{id}/documents) requires no auth.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import get_supabase_admin
from app.dependencies import get_current_user

router = APIRouter(tags=["contractor-documents"])
log    = logging.getLogger(__name__)

_VALID_TYPES = frozenset({"insurance", "licence", "certification", "other"})


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class DocumentUpload(BaseModel):
    document_type: str = Field(
        ...,
        description="One of: insurance, licence, certification, other",
    )
    file_name: str = Field(..., min_length=1, max_length=255)
    file_source: str = Field(
        ...,
        description="HTTPS URL or base64 data URI of the document image or scan.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_admin()


def _get_contractor_or_403(user_id: str) -> dict:
    """Return the contractor row for the given auth user, or raise 403."""
    res = (
        _db()
        .table("contractors")
        .select("id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=403,
            detail="Only registered contractors may manage documents",
        )
    return res.data[0]


def _get_document_or_404(doc_id: str, contractor_id: str) -> dict:
    res = (
        _db()
        .table("contractor_documents")
        .select("*")
        .eq("id", doc_id)
        .eq("contractor_id", contractor_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Document not found")
    return res.data[0]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/contractors/me/documents", status_code=201)
async def upload_document(
    body: DocumentUpload,
    user=Depends(get_current_user),
):
    """Upload an official document. AI field extraction runs immediately.

    On success the record is returned with status 'verified' (all required
    fields extracted) or 'needs_review' (poor scan / wrong document type).
    """
    if body.document_type not in _VALID_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"document_type must be one of: {', '.join(sorted(_VALID_TYPES))}",
        )

    user_id    = str(user.id)
    contractor = _get_contractor_or_403(user_id)

    try:
        from app.services import document_verifier
        result = await document_verifier.verify_document(
            image_source=body.file_source,
            document_type=body.document_type,
        )
        status             = result.status
        extracted_data     = result.extracted_data
        verification_notes = result.verification_notes
        expires_at         = result.expires_at
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log.warning(
            "document_verification_failed",
            extra={"user_id": user_id, "error": str(exc)},
        )
        status             = "needs_review"
        extracted_data     = {}
        verification_notes = (
            "Automated verification failed — please allow up to 24 h for manual review."
        )
        expires_at = None

    row: dict = {
        "contractor_id":      contractor["id"],
        "document_type":      body.document_type,
        "file_name":          body.file_name,
        "file_source":        body.file_source,
        "status":             status,
        "extracted_data":     extracted_data,
        "verification_notes": verification_notes,
        "expires_at":         expires_at,
    }
    if status == "verified":
        row["verified_at"] = "now()"

    res = _db().table("contractor_documents").insert(row).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to save document")

    log.info(
        "document_uploaded",
        extra={
            "user_id":       user_id,
            "contractor_id": contractor["id"],
            "doc_type":      body.document_type,
            "status":        status,
        },
    )
    return res.data[0]


@router.get("/contractors/me/documents")
async def list_own_documents(user=Depends(get_current_user)):
    """List all documents uploaded by the authenticated contractor (all statuses)."""
    user_id    = str(user.id)
    contractor = _get_contractor_or_403(user_id)

    res = (
        _db()
        .table("contractor_documents")
        .select("*")
        .eq("contractor_id", contractor["id"])
        .is_("deleted_at", "null")
        .order("uploaded_at", desc=True)
        .execute()
    )
    return res.data or []


@router.get("/contractors/{contractor_id}/documents")
async def list_contractor_documents(contractor_id: str):
    """List verified, non-expired documents for a contractor (public endpoint).

    file_source is excluded to avoid returning raw document bytes to third parties.
    """
    res = (
        _db()
        .table("contractor_documents")
        .select(
            "id,contractor_id,document_type,file_name,status,"
            "extracted_data,verification_notes,expires_at,verified_at,uploaded_at"
        )
        .eq("contractor_id", contractor_id)
        .eq("status", "verified")
        .is_("deleted_at", "null")
        .execute()
    )

    # PostgREST doesn't support `expires_at > now() OR expires_at IS NULL` in one
    # filter, so we do the expiry check in Python.
    now = datetime.now(timezone.utc)
    docs = [
        d for d in (res.data or [])
        if d.get("expires_at") is None
        or datetime.fromisoformat(d["expires_at"].replace("Z", "+00:00")) > now
    ]
    return docs


@router.delete("/contractors/me/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, user=Depends(get_current_user)):
    """Soft-delete a document. The record is retained for audit purposes."""
    user_id    = str(user.id)
    contractor = _get_contractor_or_403(user_id)
    doc        = _get_document_or_404(doc_id, contractor["id"])

    _db().table("contractor_documents").update({
        "deleted_at":         "now()",
        "deleted_by_user_id": user_id,
    }).eq("id", doc["id"]).execute()

    log.info(
        "document_deleted",
        extra={"user_id": user_id, "doc_id": doc_id},
    )
