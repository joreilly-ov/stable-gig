"""RFP generation service.

Takes the stored Gemini analysis result (from POST /analyse or /analyse/photos)
plus any clarification answers provided by the homeowner, and asks Gemini to
produce a structured Request for Proposal document.

The document includes:
  - Professional scope-of-work description (anonymised — no homeowner PII)
  - Trade category and urgency
  - Cost estimate range in GBP pence
  - Permit / planning considerations
  - Specific contractor requirements

The RFP is intentionally written for a contractor audience: it contains enough
technical detail to quote accurately, but omits the homeowner's name, contact
details, and full address (only the postcode is retained for regional pricing).

Uses gemini-2.0-flash (text generation only — no vision input needed at this stage).
All Gemini SDK calls are synchronous; dispatch via asyncio.to_thread.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import google.generativeai as genai

from app.config import settings

log = logging.getLogger(__name__)

_MODEL = "gemini-2.0-flash"

_RFP_SYSTEM = """You are an expert home-repair project manager writing a formal Request for Proposal (RFP).
Your output will be sent verbatim to licensed contractors so they can submit competitive bids.
Write in clear, professional British English. Use trade-accurate terminology.
Return ONLY a valid JSON object — no markdown, no preamble, no explanation."""

_RFP_PROMPT_TEMPLATE = """
Generate a structured RFP from the following home-repair assessment.

=== ANALYSIS ===
Problem type      : {problem_type}
Description       : {description}
Location in home  : {location_in_home}
Urgency           : {urgency}
Materials noted   : {materials}
Required tools    : {tools}
Postcode / region : {postcode}

=== HOMEOWNER CLARIFICATIONS ===
{clarifications_block}

=== OUTPUT SCHEMA ===
Return a JSON object with EXACTLY these fields:

{{
  "title": "Short job title (max 10 words)",
  "executive_summary": "2–3 sentence plain-English description for contractors",
  "scope_of_work": "Detailed paragraph describing all work to be completed",
  "trade_category": "Primary trade required (e.g. plumbing, electrical, roofing)",
  "urgency": "low | medium | high | emergency",
  "location_in_home": "Where the work takes place",
  "materials_noted": ["list", "of", "materials/components"],
  "special_requirements": "Any access, timing, or certification requirements (empty string if none)",
  "permit_required": true or false,
  "permit_notes": "Explanation of any planning permission or building regs considerations (empty string if none)",
  "cost_estimate": {{
    "low_pence": integer (lower bound in GBP pence, e.g. 180000 for £1800),
    "high_pence": integer (upper bound in GBP pence),
    "currency": "GBP",
    "basis": "One sentence explaining what drives the cost range"
  }},
  "contractor_requirements": "Required licences, insurance, or certifications for this job",
  "bid_deadline_days": integer (recommended bidding window — typically 3–7)
}}

Cost estimate guidance:
- Base estimates on current UK labour + material rates.
- The range should reflect genuine market uncertainty, not false precision.
- For emergency jobs widen the range by 20–30% to account for call-out premiums.
- Express amounts in pence (£1 = 100p).
"""


def _build_clarifications_block(answers: dict[str, str] | None) -> str:
    if not answers:
        return "(none provided)"
    lines = [f"Q: {q}\nA: {a}" for q, a in answers.items()]
    return "\n\n".join(lines)


def _call_gemini(prompt: str) -> str:
    """Synchronous Gemini call. Intended to run via asyncio.to_thread."""
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(
        _MODEL,
        system_instruction=_RFP_SYSTEM,
    )
    response = model.generate_content(prompt)
    return response.text


def _strip_fences(raw: str) -> str:
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    return raw


async def generate(
    analysis_result: dict,
    clarification_answers: dict[str, str] | None = None,
    postcode: str = "",
) -> dict:
    """Generate a structured RFP from an analysis result.

    Args:
        analysis_result: The JSONB blob stored on the job from Gemini analysis.
        clarification_answers: Optional mapping of question → answer from the homeowner.
        postcode: Job postcode for regional cost calibration.

    Returns:
        A dict with keys matching the RFP output schema above, plus
        ``generated_at`` (ISO-8601 timestamp).

    Raises:
        ValueError: If Gemini returns unparseable or structurally invalid JSON.
        Exception:  On upstream Gemini API failures.
    """
    problem_type    = analysis_result.get("problem_type", "")
    description     = analysis_result.get("description", "")
    location        = analysis_result.get("location_in_home", "")
    urgency         = analysis_result.get("urgency", "")
    materials       = ", ".join(analysis_result.get("materials_involved", []) or [])
    tools           = ", ".join(analysis_result.get("required_tools", []) or [])
    clarifications  = _build_clarifications_block(clarification_answers)

    prompt = _RFP_PROMPT_TEMPLATE.format(
        problem_type=problem_type,
        description=description,
        location_in_home=location,
        urgency=urgency,
        materials=materials or "(not specified)",
        tools=tools or "(not specified)",
        postcode=postcode or "(not provided)",
        clarifications_block=clarifications,
    )

    raw: str = await asyncio.to_thread(_call_gemini, prompt)
    raw = _strip_fences(raw.strip())

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned non-JSON: {raw[:300]}") from exc

    # Structural validation
    required_keys = {
        "title", "executive_summary", "scope_of_work", "trade_category",
        "urgency", "location_in_home", "materials_noted", "special_requirements",
        "permit_required", "permit_notes", "cost_estimate",
        "contractor_requirements", "bid_deadline_days",
    }
    missing = required_keys - set(parsed.keys())
    if missing:
        raise ValueError(f"RFP response missing keys: {missing}")

    cost = parsed.get("cost_estimate", {})
    if not isinstance(cost, dict):
        raise ValueError("cost_estimate must be an object")
    for field in ("low_pence", "high_pence"):
        val = cost.get(field)
        if not isinstance(val, (int, float)) or val <= 0:
            raise ValueError(f"cost_estimate.{field} must be a positive number")
        cost[field] = int(val)

    bid_days = parsed.get("bid_deadline_days")
    try:
        parsed["bid_deadline_days"] = int(bid_days)
    except (TypeError, ValueError):
        parsed["bid_deadline_days"] = 5  # sensible default

    parsed["generated_at"] = datetime.now(timezone.utc).isoformat()

    log.info(
        "rfp_generated",
        extra={
            "trade_category": parsed.get("trade_category"),
            "permit_required": parsed.get("permit_required"),
            "cost_low_pence": cost.get("low_pence"),
            "cost_high_pence": cost.get("high_pence"),
        },
    )

    return parsed
