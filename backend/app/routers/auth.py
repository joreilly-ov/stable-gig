"""Auth routes — Magic Link (OTP) and Google OAuth via Supabase Auth."""

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.database import get_supabase
from app.models.schemas import MagicLinkRequest, OTPVerifyRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config")
async def auth_config():
    """Return public Supabase config needed for client-side OAuth (safe to expose)."""
    return {
        "supabase_url": settings.supabase_url,
        "anon_key": settings.supabase_anon_key,
    }


@router.post("/magic-link", status_code=202)
async def send_magic_link(body: MagicLinkRequest):
    """Send a one-time magic-link / OTP to the given email address."""
    try:
        get_supabase().auth.sign_in_with_otp({"email": body.email})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": f"Magic link sent to {body.email}"}


@router.post("/verify", response_model=TokenResponse)
async def verify_otp(body: OTPVerifyRequest):
    """Exchange an email OTP token for a Supabase session (access_token)."""
    try:
        response = get_supabase().auth.verify_otp(
            {"email": body.email, "token": body.token, "type": "email"}
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not response.session:
        raise HTTPException(status_code=400, detail="OTP verification failed — no session returned")

    return TokenResponse(
        access_token=response.session.access_token,
        user_id=str(response.user.id),
    )
