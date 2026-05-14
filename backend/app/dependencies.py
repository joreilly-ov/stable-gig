import logging

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.database import get_supabase

_bearer = HTTPBearer(auto_error=False)
_log = logging.getLogger(__name__)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
):
    """Require a valid Supabase JWT. Raises 401 if missing or invalid."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _verify_token(credentials.credentials)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
):
    """Return the Supabase user if a valid JWT is provided, else None."""
    if not credentials:
        return None
    try:
        return _verify_token(credentials.credentials)
    except HTTPException:
        return None


def _verify_token(token: str):
    try:
        supabase = get_supabase()
        response = supabase.auth.get_user(token)
        return response.user
    except Exception as exc:
        msg = str(exc)
        # "Invalid API key" means the backend's SUPABASE_ANON_KEY is wrong —
        # that is a server misconfiguration, not a bad user token.
        if "Invalid API key" in msg:
            _log.critical(
                "SUPABASE_ANON_KEY is invalid or from the wrong project — "
                "all authenticated endpoints will fail until this is corrected"
            )
            raise HTTPException(
                status_code=503,
                detail="Authentication service misconfigured; please contact support",
            )
        raise HTTPException(status_code=401, detail="Invalid or expired token")
