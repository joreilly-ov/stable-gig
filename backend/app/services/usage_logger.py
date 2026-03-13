"""Usage logging — write a row to usage_log after every Gemini call.

Fails silently: a logging failure must never break the analysis response.
Uses the service-role client (bypasses RLS) so it works regardless of auth state.
"""

import logging

log = logging.getLogger(__name__)


def log_usage(
    analysis_type: str,   # "video" | "photo"
    model:         str,
    user_id:       str | None,
    prompt_tokens:     int,
    completion_tokens: int,
    total_tokens:      int,
) -> None:
    """Insert one row into usage_log. Never raises."""
    try:
        from app.database import get_supabase_admin

        get_supabase_admin().table("usage_log").insert({
            "analysis_type":     analysis_type,
            "model":             model,
            "user_id":           user_id,
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens":      total_tokens,
        }).execute()
    except Exception as exc:
        log.warning("usage_log_failed", extra={"analysis_type": analysis_type, "error": str(exc)})
