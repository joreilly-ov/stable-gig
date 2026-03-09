"""Gemini video analysis service."""

import json
import re
import time

import google.generativeai as genai
from app.config import settings

genai.configure(api_key=settings.gemini_api_key)

ANALYSIS_PROMPT = """You are a home repair assessment assistant. Analyse this video and extract the following as JSON:
- problem_type: (e.g. plumbing, electrical, structural, damp, general)
- description: a plain English summary of the issue visible
- location_in_home: best guess at where this is (e.g. bathroom, kitchen, external wall)
- urgency: low / medium / high / emergency
- materials_involved: list of materials or components visible
- clarifying_questions: list of 2-3 questions a tradesperson would want answered before quoting

Return only valid JSON, no markdown."""


def analyse(file_path: str, mime_type: str) -> dict:
    """Upload *file_path* to Gemini, wait for it to become ACTIVE, then return the parsed JSON result."""
    uploaded = genai.upload_file(file_path, mime_type=mime_type)

    # Poll until file is ACTIVE
    while uploaded.state.name == "PROCESSING":
        time.sleep(2)
        uploaded = genai.get_file(uploaded.name)

    if uploaded.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini file entered state {uploaded.state.name!r}")

    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content([ANALYSIS_PROMPT, uploaded])

    raw_text = response.text.strip()
    # Strip markdown code fences that Gemini occasionally wraps responses in
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text).strip()

    return json.loads(raw_text)
