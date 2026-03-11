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
    """Upload *file_path* to Gemini, wait for it to become ACTIVE, then return the parsed JSON result.

    Intended to be called via asyncio.to_thread — all Gemini SDK calls are synchronous,
    so time.sleep here is safe (it runs on a worker thread, not the event loop).
    """
    uploaded = genai.upload_file(file_path, mime_type=mime_type)

    try:
        # Poll until file is ACTIVE — time.sleep is fine here; see docstring.
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

    finally:
        # [SECURITY: code-review] Always delete the remote Gemini file after analysis to
        # prevent unbounded accumulation in the Gemini File API storage quota.
        try:
            genai.delete_file(uploaded.name)
        except Exception:
            pass  # best-effort; don't mask the original error
