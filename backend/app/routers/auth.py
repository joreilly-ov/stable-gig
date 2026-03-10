"""Auth routes — Magic Link (OTP), Google OAuth, and Password auth via Supabase Auth."""

import logging

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.database import get_supabase
from app.models.schemas import (
    MagicLinkRequest,
    OTPVerifyRequest,
    PasswordAuthRequest,
    RegisterResponse,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)


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
        log.error("magic_link_failed", extra={"email": body.email, "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))
    log.info("magic_link_sent", extra={"email": body.email})
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


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register_with_password(body: PasswordAuthRequest):
    """Register a new account with email + password.

    Supabase enforces one account per email across all providers — attempting
    to register with an address already used by Google / magic-link returns a
    clear error rather than creating a duplicate.
    """
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")
    try:
        response = get_supabase().auth.sign_up(
            {"email": body.email, "password": body.password}
        )
    except Exception as exc:
        log.error("register_failed", extra={"email": body.email, "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))

    if not response.user:
        raise HTTPException(status_code=400, detail="Registration failed — please try again.")

    # Email confirmation required: user exists but session not issued yet.
    if not response.session:
        log.info("register_confirmation_required", extra={"email": body.email})
        return RegisterResponse(status="confirmation_required", user_id=str(response.user.id))

    log.info("register_success", extra={"email": body.email})
    return RegisterResponse(
        status="active",
        access_token=response.session.access_token,
        user_id=str(response.user.id),
    )


@router.post("/login/password", response_model=TokenResponse)
async def login_with_password(body: PasswordAuthRequest):
    """Sign in with email + password."""
    try:
        response = get_supabase().auth.sign_in_with_password(
            {"email": body.email, "password": body.password}
        )
    except Exception as exc:
        log.warning("password_login_failed", extra={"email": body.email, "error": str(exc)})
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not response.session:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    log.info("password_login_success", extra={"email": body.email})
    return TokenResponse(
        access_token=response.session.access_token,
        user_id=str(response.user.id),
    )
