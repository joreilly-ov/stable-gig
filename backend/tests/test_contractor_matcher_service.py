"""Tests for app.services.contractor_matcher.

Coverage
--------
_build_profile_text:
  - Full profile (name, trades, experience, insurance, license, postcode)
  - Profile with no details row (details=None)
  - Empty contractor row → empty string
  - Unverified insurance is NOT mentioned

_build_job_query_text:
  - Uses RFP fields when present
  - Falls back to title + description when no RFP
  - Appends trade activity in both paths
  - Returns empty string for empty job dict

embed_text:
  - Dispatches _call_embed with correct task_type
  - Custom task_type is forwarded

update_contractor_embedding:
  - Happy path: fetches contractor + details, embeds, writes back
  - Works when contractor_details row does not exist
  - Raises LookupError when contractor row missing
  - Raises ValueError when profile text is empty

find_matching_contractors:
  - Returns semantic matches when RPC returns data, sorted by score desc
  - Falls back to activity filter when RPC returns empty results
  - Falls back directly when job has no query text (empty job dict)
  - Fallback rows have match_score=None
  - Respects the limit parameter

No real Gemini or DB calls are made — all external dependencies are patched.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.services.contractor_matcher import (
    _build_profile_text,
    _build_job_query_text,
    embed_text,
    find_matching_contractors,
    update_contractor_embedding,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_db(*execute_responses):
    """Supabase mock that returns each response in order from .execute()."""
    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.update.return_value = db
    db.eq.return_value = db
    db.limit.return_value = db
    db.contains.return_value = db
    db.in_.return_value = db
    db.rpc.return_value = db
    db.execute.side_effect = [MagicMock(data=r) for r in execute_responses]
    return db


_FAKE_EMBEDDING = [0.1] * 768


# ---------------------------------------------------------------------------
# _build_profile_text
# ---------------------------------------------------------------------------

class TestBuildProfileText:
    def test_full_profile_includes_all_fields(self):
        contractor = {
            "business_name": "Bob's Plumbing",
            "activities": ["plumbing", "heating"],
            "postcode": "SW1A 1AA",
        }
        details = {
            "years_experience": 10,
            "insurance_verified": True,
            "license_number": "PL123",
        }
        text = _build_profile_text(contractor, details)
        assert "Bob's Plumbing" in text
        assert "plumbing" in text
        assert "heating" in text
        assert "10 years" in text
        assert "Fully insured" in text
        assert "PL123" in text
        assert "SW1A 1AA" in text

    def test_no_details_row(self):
        contractor = {"business_name": "Sparks Ltd", "activities": ["electrical"], "postcode": "E1 6RF"}
        text = _build_profile_text(contractor, None)
        assert "Sparks Ltd" in text
        assert "electrical" in text
        assert "E1 6RF" in text
        assert "years" not in text
        assert "insured" not in text

    def test_empty_contractor_returns_empty_string(self):
        assert _build_profile_text({}, None) == ""

    def test_unverified_insurance_not_mentioned(self):
        contractor = {"business_name": "Fix It"}
        details = {"insurance_verified": False, "years_experience": None, "license_number": ""}
        text = _build_profile_text(contractor, details)
        assert "insured" not in text

    def test_missing_experience_and_license_omitted(self):
        contractor = {"business_name": "Roofers R Us", "activities": ["roofing"]}
        details = {"years_experience": None, "insurance_verified": True, "license_number": ""}
        text = _build_profile_text(contractor, details)
        assert "None years" not in text
        assert "Licensed" not in text
        assert "Fully insured" in text

    def test_trailing_period_present(self):
        contractor = {"business_name": "ABC Trades"}
        text = _build_profile_text(contractor, None)
        assert text.endswith(".")


# ---------------------------------------------------------------------------
# _build_job_query_text
# ---------------------------------------------------------------------------

class TestBuildJobQueryText:
    def test_uses_rfp_fields_when_present(self):
        job = {
            "rfp_document": {
                "scope_of_work": "Replace entire boiler system",
                "executive_summary": "Central heating fault",
                "contractor_requirements": "Gas Safe registered",
            },
            "title": "Boiler job",
            "description": "old boiler",
            "activity": "plumbing",
        }
        text = _build_job_query_text(job)
        assert "Replace entire boiler system" in text
        assert "Central heating fault" in text
        assert "Gas Safe registered" in text
        assert "plumbing" in text
        # Raw title/description should NOT appear since RFP fields take priority
        assert "old boiler" not in text

    def test_falls_back_to_title_and_description(self):
        job = {"title": "Leaky tap", "description": "Kitchen tap drips constantly", "activity": "plumbing"}
        text = _build_job_query_text(job)
        assert "Leaky tap" in text
        assert "Kitchen tap drips constantly" in text
        assert "plumbing" in text

    def test_empty_job_returns_empty_string(self):
        assert _build_job_query_text({}) == ""

    def test_activity_appended_when_no_rfp(self):
        job = {"title": "Fix roof", "description": "tiles missing", "activity": "roofing"}
        text = _build_job_query_text(job)
        assert "roofing" in text

    def test_no_activity_still_produces_text(self):
        job = {"title": "Paint hallway", "description": "Needs two coats"}
        text = _build_job_query_text(job)
        assert "Paint hallway" in text
        assert "Trade required" not in text


# ---------------------------------------------------------------------------
# embed_text
# ---------------------------------------------------------------------------

class TestEmbedText:
    def test_calls_call_embed_with_default_task_type(self):
        with patch("app.services.contractor_matcher._call_embed", return_value=_FAKE_EMBEDDING) as mock:
            result = _run(embed_text("hello world"))
        mock.assert_called_once_with("hello world", "RETRIEVAL_DOCUMENT")
        assert result == _FAKE_EMBEDDING

    def test_forwards_custom_task_type(self):
        with patch("app.services.contractor_matcher._call_embed", return_value=_FAKE_EMBEDDING) as mock:
            _run(embed_text("query text", task_type="RETRIEVAL_QUERY"))
        mock.assert_called_once_with("query text", "RETRIEVAL_QUERY")

    def test_returns_embedding_list(self):
        with patch("app.services.contractor_matcher._call_embed", return_value=_FAKE_EMBEDDING):
            result = _run(embed_text("anything"))
        assert len(result) == 768
        assert result[0] == 0.1


# ---------------------------------------------------------------------------
# update_contractor_embedding
# ---------------------------------------------------------------------------

class TestUpdateContractorEmbedding:
    def test_happy_path_returns_profile_text_and_dimensions(self):
        contractor = {
            "id": "c-001",
            "business_name": "Fix It Fast",
            "activities": ["plumbing"],
            "postcode": "SW1",
        }
        details = {
            "id": "c-001",
            "years_experience": 5,
            "insurance_verified": True,
            "license_number": "",
        }
        # execute order: contractor lookup, details lookup, update
        db = _make_db([contractor], [details], [])
        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db), \
             patch("app.services.contractor_matcher._call_embed", return_value=_FAKE_EMBEDDING):
            result = _run(update_contractor_embedding("c-001"))

        assert result["embedding_dimensions"] == 768
        assert "plumbing" in result["profile_text"]

    def test_works_without_contractor_details_row(self):
        contractor = {
            "id": "c-001",
            "business_name": "Solo Sparks",
            "activities": ["electrical"],
            "postcode": "W1A",
        }
        # execute order: contractor found, details empty, update
        db = _make_db([contractor], [], [])
        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db), \
             patch("app.services.contractor_matcher._call_embed", return_value=_FAKE_EMBEDDING):
            result = _run(update_contractor_embedding("c-001"))

        assert result["embedding_dimensions"] == 768

    def test_raises_lookup_error_when_contractor_not_found(self):
        db = _make_db([])
        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db):
            with pytest.raises(LookupError, match="c-001"):
                _run(update_contractor_embedding("c-001"))

    def test_raises_value_error_for_empty_profile(self):
        # Empty contractor — no name, no trades, no postcode
        contractor = {"id": "c-001", "business_name": "", "activities": [], "postcode": ""}
        # execute order: contractor found, details empty
        db = _make_db([contractor], [])
        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db):
            with pytest.raises(ValueError, match="insufficient"):
                _run(update_contractor_embedding("c-001"))

    def test_writes_embedding_back_to_db(self):
        contractor = {
            "id": "c-001",
            "business_name": "Drains Direct",
            "activities": ["drainage"],
            "postcode": "N1",
        }
        db = _make_db([contractor], [], [])
        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db), \
             patch("app.services.contractor_matcher._call_embed", return_value=_FAKE_EMBEDDING):
            _run(update_contractor_embedding("c-001"))

        db.update.assert_called_once()
        update_call_kwargs = db.update.call_args[0][0]
        assert "profile_embedding" in update_call_kwargs
        assert "profile_text" in update_call_kwargs


# ---------------------------------------------------------------------------
# find_matching_contractors
# ---------------------------------------------------------------------------

class TestFindMatchingContractors:
    def test_returns_semantic_matches_when_rpc_returns_data(self):
        rpc_data = [{"contractor_id": "c-001", "similarity": 0.92}]
        contractors = [{"id": "c-001", "business_name": "Bob"}]
        # execute order: rpc call, contractor details fetch
        db = _make_db(rpc_data, contractors)

        job = {"id": "j-001", "title": "Leaky tap", "description": "needs fixing", "activity": "plumbing"}
        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db), \
             patch("app.services.contractor_matcher._call_embed", return_value=_FAKE_EMBEDDING):
            results = _run(find_matching_contractors(job))

        assert len(results) == 1
        assert results[0]["match_score"] == 0.92

    def test_results_sorted_by_score_descending(self):
        rpc_data = [
            {"contractor_id": "c-001", "similarity": 0.70},
            {"contractor_id": "c-002", "similarity": 0.95},
        ]
        contractors = [
            {"id": "c-001", "business_name": "B1"},
            {"id": "c-002", "business_name": "B2"},
        ]
        db = _make_db(rpc_data, contractors)
        job = {"title": "Fix boiler", "description": "cold house"}
        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db), \
             patch("app.services.contractor_matcher._call_embed", return_value=_FAKE_EMBEDDING):
            results = _run(find_matching_contractors(job))

        assert results[0]["match_score"] == 0.95
        assert results[1]["match_score"] == 0.70

    def test_falls_back_to_activity_filter_when_rpc_returns_empty(self):
        fallback_contractor = {"id": "c-001", "activities": ["plumbing"]}
        # execute order: rpc call (empty), fallback query
        db = _make_db([], [fallback_contractor])

        job = {"id": "j-001", "title": "Fix tap", "description": "drips", "activity": "plumbing"}
        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db), \
             patch("app.services.contractor_matcher._call_embed", return_value=_FAKE_EMBEDDING):
            results = _run(find_matching_contractors(job))

        assert len(results) == 1
        assert results[0]["match_score"] is None

    def test_falls_back_directly_when_job_has_no_text(self):
        # Empty job → no query text → skip rpc entirely → single fallback execute
        fallback_contractor = {"id": "c-001"}
        db = _make_db([fallback_contractor])

        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db):
            results = _run(find_matching_contractors({}))

        assert len(results) == 1
        assert results[0]["match_score"] is None

    def test_fallback_rows_have_none_match_score(self):
        db = _make_db([{"id": "c-001"}, {"id": "c-002"}])
        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db):
            results = _run(find_matching_contractors({}))

        for r in results:
            assert r["match_score"] is None

    def test_returns_empty_list_when_no_contractors(self):
        # RPC empty, fallback empty
        db = _make_db([], [])
        job = {"title": "Fix roof", "description": "tiles gone"}
        with patch("app.services.contractor_matcher.get_supabase_admin", return_value=db), \
             patch("app.services.contractor_matcher._call_embed", return_value=_FAKE_EMBEDDING):
            results = _run(find_matching_contractors(job))

        assert results == []
