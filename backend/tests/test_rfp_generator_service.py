"""Tests for app.services.rfp_generator.

Coverage
--------
_build_clarifications_block:
  - Returns "(none provided)" when answers is None or empty
  - Formats Q/A pairs correctly

_strip_fences:
  - Passes through plain JSON unchanged
  - Strips ``` fences
  - Strips ```json fences

generate():
  - Happy path: returns parsed dict with generated_at timestamp
  - Builds prompt from all analysis_result fields
  - Handles missing/empty optional fields gracefully
  - Passes postcode into prompt
  - Passes clarification_answers into prompt
  - Raises ValueError on non-JSON Gemini response
  - Raises ValueError when required top-level keys are missing
  - Raises ValueError when cost_estimate is not a dict
  - Raises ValueError when cost_estimate.low_pence is missing or zero
  - Raises ValueError when cost_estimate.high_pence is missing or zero
  - Coerces float cost values to int
  - Coerces non-integer bid_deadline_days to default 5
  - Adds generated_at timestamp to returned dict

No real Gemini calls are made — _call_gemini is patched throughout.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.services.rfp_generator import (
    _build_clarifications_block,
    _strip_fences,
    generate,
)


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_VALID_RFP = {
    "title": "Boiler replacement",
    "executive_summary": "Central heating system failure requiring full replacement",
    "scope_of_work": "Remove old boiler, install new combi boiler, commission system",
    "trade_category": "plumbing",
    "urgency": "high",
    "location_in_home": "utility room",
    "materials_noted": ["combi boiler", "copper pipe", "fittings"],
    "special_requirements": "Gas Safe registration required",
    "permit_required": False,
    "permit_notes": "",
    "cost_estimate": {
        "low_pence": 250_000,
        "high_pence": 400_000,
        "currency": "GBP",
        "basis": "Labour and materials at current UK rates",
    },
    "contractor_requirements": "Gas Safe registered engineer with combi boiler experience",
    "bid_deadline_days": 5,
}

_ANALYSIS = {
    "problem_type": "heating",
    "description": "Boiler has stopped working completely",
    "location_in_home": "utility room",
    "urgency": "high",
    "materials_involved": ["boiler", "pipes", "radiators"],
    "required_tools": ["spanner", "gas analyser"],
}


def _gemini_returns(obj: dict):
    """Patch _call_gemini to return a JSON string of obj."""
    return patch(
        "app.services.rfp_generator._call_gemini",
        return_value=json.dumps(obj),
    )


# ---------------------------------------------------------------------------
# _build_clarifications_block
# ---------------------------------------------------------------------------

class TestBuildClarificationsBlock:
    def test_none_returns_none_provided(self):
        assert _build_clarifications_block(None) == "(none provided)"

    def test_empty_dict_returns_none_provided(self):
        assert _build_clarifications_block({}) == "(none provided)"

    def test_single_qa_pair_formatted_correctly(self):
        result = _build_clarifications_block({"How old is the boiler?": "About 15 years"})
        assert "Q: How old is the boiler?" in result
        assert "A: About 15 years" in result

    def test_multiple_qa_pairs_all_present(self):
        answers = {
            "Is there asbestos?": "No",
            "Access available?": "Yes, weekdays only",
        }
        result = _build_clarifications_block(answers)
        assert "Is there asbestos?" in result
        assert "No" in result
        assert "Access available?" in result
        assert "weekdays only" in result


# ---------------------------------------------------------------------------
# _strip_fences
# ---------------------------------------------------------------------------

class TestStripFences:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert _strip_fences(raw) == raw

    def test_strips_plain_backtick_fences(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = _strip_fences(raw)
        assert result == '{"key": "value"}'

    def test_strips_json_labelled_fences(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = _strip_fences(raw)
        assert result == '{"key": "value"}'

    def test_no_fence_when_not_starting_with_backtick(self):
        raw = 'plain text without fences'
        assert _strip_fences(raw) == raw


# ---------------------------------------------------------------------------
# generate() — happy path
# ---------------------------------------------------------------------------

class TestGenerateHappyPath:
    def test_returns_all_required_keys(self):
        with _gemini_returns(_VALID_RFP):
            result = _run_generate(_ANALYSIS)

        required = {
            "title", "executive_summary", "scope_of_work", "trade_category",
            "urgency", "location_in_home", "materials_noted", "special_requirements",
            "permit_required", "permit_notes", "cost_estimate",
            "contractor_requirements", "bid_deadline_days", "generated_at",
        }
        assert required.issubset(result.keys())

    def test_adds_generated_at_timestamp(self):
        with _gemini_returns(_VALID_RFP):
            result = _run_generate(_ANALYSIS)

        ts = result["generated_at"]
        assert "T" in ts
        # Must parse as a valid datetime
        datetime.fromisoformat(ts)

    def test_passes_problem_type_into_prompt(self):
        with patch("app.services.rfp_generator._call_gemini", return_value=json.dumps(_VALID_RFP)) as mock:
            _run_generate(_ANALYSIS)

        prompt_text = mock.call_args[0][0]
        assert "heating" in prompt_text

    def test_passes_postcode_into_prompt(self):
        with patch("app.services.rfp_generator._call_gemini", return_value=json.dumps(_VALID_RFP)) as mock:
            _run_generate(_ANALYSIS, postcode="SW1A 1AA")

        prompt_text = mock.call_args[0][0]
        assert "SW1A 1AA" in prompt_text

    def test_passes_clarification_answers_into_prompt(self):
        with patch("app.services.rfp_generator._call_gemini", return_value=json.dumps(_VALID_RFP)) as mock:
            _run_generate(_ANALYSIS, clarification_answers={"Age of boiler?": "15 years"})

        prompt_text = mock.call_args[0][0]
        assert "Age of boiler?" in prompt_text
        assert "15 years" in prompt_text

    def test_handles_missing_optional_analysis_fields(self):
        minimal = {"problem_type": "plumbing", "description": "Leaky pipe"}
        with _gemini_returns(_VALID_RFP):
            result = _run_generate(minimal)

        assert result["title"] == "Boiler replacement"

    def test_cost_pence_values_coerced_to_int(self):
        rfp_with_floats = {
            **_VALID_RFP,
            "cost_estimate": {**_VALID_RFP["cost_estimate"], "low_pence": 250000.0, "high_pence": 400000.9},
        }
        with _gemini_returns(rfp_with_floats):
            result = _run_generate(_ANALYSIS)

        assert isinstance(result["cost_estimate"]["low_pence"], int)
        assert isinstance(result["cost_estimate"]["high_pence"], int)
        assert result["cost_estimate"]["low_pence"] == 250_000

    def test_bid_deadline_days_coerced_when_float(self):
        rfp_float_days = {**_VALID_RFP, "bid_deadline_days": 4.7}
        with _gemini_returns(rfp_float_days):
            result = _run_generate(_ANALYSIS)

        assert isinstance(result["bid_deadline_days"], int)
        assert result["bid_deadline_days"] == 4

    def test_bid_deadline_days_defaults_to_5_when_invalid(self):
        rfp_bad_days = {**_VALID_RFP, "bid_deadline_days": "not-a-number"}
        with _gemini_returns(rfp_bad_days):
            result = _run_generate(_ANALYSIS)

        assert result["bid_deadline_days"] == 5

    def test_bid_deadline_days_defaults_to_5_when_none(self):
        rfp_null_days = {**_VALID_RFP, "bid_deadline_days": None}
        with _gemini_returns(rfp_null_days):
            result = _run_generate(_ANALYSIS)

        assert result["bid_deadline_days"] == 5


# ---------------------------------------------------------------------------
# generate() — error cases
# ---------------------------------------------------------------------------

class TestGenerateErrors:
    def test_raises_value_error_on_non_json_response(self):
        with patch("app.services.rfp_generator._call_gemini", return_value="not valid json at all"):
            with pytest.raises(ValueError, match="non-JSON"):
                _run_generate(_ANALYSIS)

    def test_raises_value_error_on_missing_required_key(self):
        incomplete = {k: v for k, v in _VALID_RFP.items() if k != "title"}
        with _gemini_returns(incomplete):
            with pytest.raises(ValueError, match="missing keys"):
                _run_generate(_ANALYSIS)

    def test_raises_value_error_when_multiple_keys_missing(self):
        stripped = {k: v for k, v in _VALID_RFP.items()
                    if k not in ("title", "scope_of_work", "trade_category")}
        with _gemini_returns(stripped):
            with pytest.raises(ValueError, match="missing keys"):
                _run_generate(_ANALYSIS)

    def test_raises_value_error_when_cost_estimate_not_dict(self):
        bad_rfp = {**_VALID_RFP, "cost_estimate": "£2000"}
        with _gemini_returns(bad_rfp):
            with pytest.raises(ValueError, match="cost_estimate must be an object"):
                _run_generate(_ANALYSIS)

    def test_raises_value_error_when_low_pence_missing(self):
        bad_cost = {k: v for k, v in _VALID_RFP["cost_estimate"].items() if k != "low_pence"}
        bad_rfp = {**_VALID_RFP, "cost_estimate": bad_cost}
        with _gemini_returns(bad_rfp):
            with pytest.raises(ValueError, match="low_pence"):
                _run_generate(_ANALYSIS)

    def test_raises_value_error_when_low_pence_is_zero(self):
        bad_cost = {**_VALID_RFP["cost_estimate"], "low_pence": 0}
        bad_rfp = {**_VALID_RFP, "cost_estimate": bad_cost}
        with _gemini_returns(bad_rfp):
            with pytest.raises(ValueError, match="low_pence"):
                _run_generate(_ANALYSIS)

    def test_raises_value_error_when_high_pence_missing(self):
        bad_cost = {k: v for k, v in _VALID_RFP["cost_estimate"].items() if k != "high_pence"}
        bad_rfp = {**_VALID_RFP, "cost_estimate": bad_cost}
        with _gemini_returns(bad_rfp):
            with pytest.raises(ValueError, match="high_pence"):
                _run_generate(_ANALYSIS)

    def test_raises_value_error_when_high_pence_negative(self):
        bad_cost = {**_VALID_RFP["cost_estimate"], "high_pence": -100}
        bad_rfp = {**_VALID_RFP, "cost_estimate": bad_cost}
        with _gemini_returns(bad_rfp):
            with pytest.raises(ValueError, match="high_pence"):
                _run_generate(_ANALYSIS)

    def test_strips_fences_before_parsing(self):
        fenced = f"```json\n{json.dumps(_VALID_RFP)}\n```"
        with patch("app.services.rfp_generator._call_gemini", return_value=fenced):
            result = _run_generate(_ANALYSIS)
        assert result["title"] == "Boiler replacement"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

import asyncio


def _run_generate(analysis, *, clarification_answers=None, postcode=""):
    return asyncio.get_event_loop().run_until_complete(
        generate(analysis, clarification_answers=clarification_answers, postcode=postcode)
    )
