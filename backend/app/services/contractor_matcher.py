"""Contractor matching service — profile embeddings + semantic similarity.

Flow
----
1. When a contractor updates their profile, they call ``update_contractor_embedding()``
   which composes a plain-text summary of their profile and stores a 768-dim
   embedding in ``contractor_details.profile_embedding``.

2. When a homeowner publishes a job, ``find_matching_contractors()`` embeds the
   RFP text (or falls back to job title + description) and queries the Postgres
   ``match_contractors`` RPC function for cosine-similarity matches.

3. If no contractor has generated an embedding yet (empty vector column), the
   service falls back to a plain activity-based filter so the endpoint always
   returns a useful result.

Embedding model: Gemini ``text-embedding-004`` (768 dimensions).
All Gemini SDK calls are synchronous; dispatched via asyncio.to_thread.
"""

import asyncio
import logging

import google.generativeai as genai

from app.config import settings
from app.database import get_supabase_admin

log = logging.getLogger(__name__)

_EMBED_MODEL = "models/text-embedding-004"


# ---------------------------------------------------------------------------
# Low-level embedding helpers
# ---------------------------------------------------------------------------

def _call_embed(text: str, task_type: str) -> list[float]:
    """Synchronous Gemini embedding call. Runs via asyncio.to_thread."""
    genai.configure(api_key=settings.gemini_api_key)
    result = genai.embed_content(
        model=_EMBED_MODEL,
        content=text,
        task_type=task_type,
    )
    return result["embedding"]


async def embed_text(
    text: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[float]:
    """Return a 768-dimensional embedding for *text*.

    Args:
        text: The text to embed.
        task_type: ``"RETRIEVAL_DOCUMENT"`` for contractor profiles;
                   ``"RETRIEVAL_QUERY"`` for job/RFP queries.

    Returns:
        List of 768 floats.
    """
    return await asyncio.to_thread(_call_embed, text, task_type)


# ---------------------------------------------------------------------------
# Contractor profile embedding
# ---------------------------------------------------------------------------

def _build_profile_text(contractor: dict, details: dict | None) -> str:
    """Compose a plain-text summary of a contractor's profile for embedding."""
    parts: list[str] = []

    name = (contractor.get("business_name") or "").strip()
    if name:
        parts.append(name)

    activities = contractor.get("activities") or []
    if activities:
        parts.append(f"Trades: {', '.join(activities)}")

    if details:
        years = details.get("years_experience")
        if years:
            parts.append(f"{years} years of experience")
        if details.get("insurance_verified"):
            parts.append("Fully insured")
        license_no = details.get("license_number") or ""
        if license_no:
            parts.append(f"Licensed (ref: {license_no})")

    postcode = (contractor.get("postcode") or "").strip()
    if postcode:
        parts.append(f"Based in {postcode}")

    return ". ".join(parts) + "." if parts else ""


async def update_contractor_embedding(contractor_id: str) -> dict:
    """Regenerate and store the embedding for contractor *contractor_id*.

    Fetches the latest contractor + contractor_details rows, builds a
    profile-text summary, embeds it, then writes the result back.

    Returns:
        Dict with ``profile_text`` and ``embedding_dimensions``.

    Raises:
        LookupError: If the contractor row does not exist.
    """
    db = get_supabase_admin()

    c_res = db.table("contractors").select("*").eq("id", contractor_id).limit(1).execute()
    if not c_res.data:
        raise LookupError(f"Contractor {contractor_id!r} not found")
    contractor = c_res.data[0]

    d_res = (
        db.table("contractor_details")
        .select("*")
        .eq("id", contractor_id)
        .limit(1)
        .execute()
    )
    details = d_res.data[0] if d_res.data else None

    profile_text = _build_profile_text(contractor, details)
    if not profile_text:
        raise ValueError("Contractor profile has insufficient data to embed")

    embedding = await embed_text(profile_text, task_type="RETRIEVAL_DOCUMENT")

    db.table("contractor_details").update(
        {"profile_embedding": embedding, "profile_text": profile_text}
    ).eq("id", contractor_id).execute()

    log.info(
        "contractor_embedding_updated",
        extra={"contractor_id": contractor_id, "profile_chars": len(profile_text)},
    )
    return {"profile_text": profile_text, "embedding_dimensions": len(embedding)}


# ---------------------------------------------------------------------------
# Job ↔ contractor matching
# ---------------------------------------------------------------------------

def _build_job_query_text(job: dict) -> str:
    """Compose a query string from the job record for embedding."""
    parts: list[str] = []

    rfp = job.get("rfp_document") or {}
    if rfp.get("scope_of_work"):
        parts.append(rfp["scope_of_work"])
    if rfp.get("executive_summary"):
        parts.append(rfp["executive_summary"])
    if rfp.get("contractor_requirements"):
        parts.append(rfp["contractor_requirements"])

    # Fallback to raw job fields if RFP not yet generated
    if not parts:
        title = (job.get("title") or "").strip()
        desc  = (job.get("description") or "").strip()
        if title:
            parts.append(title)
        if desc:
            parts.append(desc)

    activity = (job.get("activity") or "").strip()
    if activity:
        parts.append(f"Trade required: {activity}")

    return " ".join(parts)


async def find_matching_contractors(
    job: dict,
    limit: int = 10,
) -> list[dict]:
    """Return up to *limit* contractors ranked by suitability for *job*.

    Strategy:
    1. Build a query text from the job's RFP (or raw description as fallback).
    2. Embed the text with task_type="RETRIEVAL_QUERY".
    3. Call the ``match_contractors`` Postgres RPC function.
    4. Fetch full contractor rows for the returned IDs and merge similarity scores.
    5. If no embeddings exist in the DB, fall back to activity-based filtering.

    Returns:
        List of contractor dicts (from the ``contractors`` table) each augmented
        with a ``match_score`` float (0–1, or ``None`` for activity-fallback rows).
    """
    db = get_supabase_admin()
    activity = job.get("activity") or None

    query_text = _build_job_query_text(job)

    if query_text.strip():
        embedding = await embed_text(query_text, task_type="RETRIEVAL_QUERY")

        rpc_res = db.rpc(
            "match_contractors",
            {
                "query_embedding": embedding,
                "match_activity": activity,
                "match_limit": limit,
            },
        ).execute()

        if rpc_res.data:
            similarity_map: dict[str, float] = {
                row["contractor_id"]: float(row["similarity"])
                for row in rpc_res.data
            }
            ids = list(similarity_map.keys())

            c_res = (
                db.table("contractors")
                .select("*, contractor_details(*)")
                .in_("id", ids)
                .execute()
            )

            results: list[dict] = []
            for c in (c_res.data or []):
                c["match_score"] = similarity_map.get(c["id"], 0.0)
                results.append(c)

            results.sort(key=lambda x: x["match_score"], reverse=True)

            log.info(
                "contractor_match_embedding",
                extra={"job_id": job.get("id"), "matches": len(results)},
            )
            return results

    # Fallback — activity filter only (no embeddings in DB yet)
    log.info(
        "contractor_match_fallback",
        extra={"job_id": job.get("id"), "activity": activity},
    )

    query = db.table("contractors").select("*, contractor_details(*)")
    if activity:
        query = query.contains("activities", [activity])
    fallback_res = query.limit(limit).execute()

    for c in (fallback_res.data or []):
        c["match_score"] = None

    return fallback_res.data or []
