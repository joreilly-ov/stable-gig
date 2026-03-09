"""
Supabase client singletons.

- `get_supabase()` — anon-key client; subject to RLS. Use for all user-facing queries.
- `get_supabase_admin()` — service-role client; bypasses RLS. Use only for admin operations
  (e.g. inserting trade records) and never expose to the public.
"""

from supabase import create_client, Client
from app.config import settings

_client: Client | None = None
_admin_client: Client | None = None


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
