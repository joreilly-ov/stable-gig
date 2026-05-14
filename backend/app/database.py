"""
Supabase client singletons.

- `get_supabase()` — anon-key client; subject to RLS. Use for all user-facing queries.
- `get_supabase_admin()` — service-role client; bypasses RLS. Use only for admin operations
  (e.g. inserting trade records) and never expose to the public.
"""

import logging

from supabase import create_client, Client
from app.config import settings

_client: Client | None = None
_admin_client: Client | None = None
_log = logging.getLogger(__name__)


def get_supabase() -> Client:
    global _client
    if _client is None:
        if not settings.supabase_url or not settings.supabase_anon_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set")
        _client = create_client(settings.supabase_url, settings.supabase_anon_key)
    return _client


def get_supabase_admin() -> Client:
    global _admin_client
    if _admin_client is None:
        if not settings.supabase_url or not settings.supabase_service_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        _admin_client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _admin_client


def probe_supabase_anon_key() -> None:
    """Validate SUPABASE_ANON_KEY at startup by making a lightweight auth call.

    Logs CRITICAL and raises RuntimeError if the key is rejected by Supabase,
    so misconfiguration is surfaced immediately in Cloud Run startup logs rather
    than silently at the first authenticated request.
    """
    if not settings.supabase_url or not settings.supabase_anon_key:
        return  # missing-key guard already handled by get_supabase()
    try:
        client = get_supabase()
        # get_user with a deliberately invalid token returns an auth error but
        # still requires a valid apikey header — if the anon key is wrong,
        # Supabase returns "Invalid API key" before even checking the JWT.
        client.auth.get_user("probe")
    except Exception as exc:
        msg = str(exc)
        if "Invalid API key" in msg:
            _log.critical(
                "SUPABASE_ANON_KEY is invalid or from the wrong Supabase project. "
                "All authenticated endpoints will return 503 until this is corrected. "
                "Update the secret in GCP Secret Manager and redeploy."
            )
            raise RuntimeError(f"SUPABASE_ANON_KEY rejected by Supabase: {msg}") from exc
        # Any other error (e.g. invalid JWT on the probe token) is expected — key is fine.
