"""Auth routes — Magic Link (OTP), Google OAuth, and Password auth via Supabase Auth."""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.database import get_supabase
from app.models.schemas import (
    MagicLinkRequest,
    OTPVerifyRequest,
    PasswordAuthRequest,
    PasswordResetRequest,
    PasswordUpdateRequest,
    RegisterResponse,
    TokenResponse,
)

# [SECURITY: code-review] Import the shared limiter to apply per-IP rate limits
# on auth endpoints, preventing email-bombing and brute-force attacks.
from main import limiter

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
@limiter.limit("5/minute")  # [SECURITY: code-review] Prevent email-bombing
async def send_magic_link(request: Request, body: MagicLinkRequest):
    """Send a one-time magic-link / OTP to the given email address."""
    try:
        get_supabase().auth.sign_in_with_otp({"email": body.email})
    except Exception as exc:
        log.error("magic_link_failed", extra={"email": body.email, "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))
    log.info("magic_link_sent", extra={"email": body.email})
    return {"message": f"Magic link sent to {body.email}"}


@router.post("/verify", response_model=TokenResponse)
@limiter.limit("10/minute")  # [SECURITY: code-review] Limit OTP guessing attempts
async def verify_otp(request: Request, body: OTPVerifyRequest):
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
@limiter.limit("5/minute")  # [SECURITY: code-review] Prevent account enumeration spam
async def register_with_password(request: Request, body: PasswordAuthRequest):
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
@limiter.limit("10/minute")  # [SECURITY: code-review] Limit password brute-force attempts
async def login_with_password(request: Request, body: PasswordAuthRequest):
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


@router.post("/forgot-password", status_code=202)
@limiter.limit("3/minute")  # [SECURITY: code-review] Prevent email-bombing
async def forgot_password(request: Request, body: PasswordResetRequest):
    """Send a password-reset email.  Always returns 202 to avoid leaking whether
    an address is registered (email enumeration defence)."""
    try:
        get_supabase().auth.reset_password_for_email(body.email)
    except Exception as exc:
        # Log but don't expose the error — still return 202 to prevent enumeration
        log.warning("forgot_password_error", extra={"email": body.email, "error": str(exc)})
    log.info("forgot_password_sent", extra={"email": body.email})
    return {"message": "If that address is registered you will receive a reset link shortly."}


@router.post("/reset-password", response_model=TokenResponse)
@limiter.limit("5/minute")  # [SECURITY: code-review] Limit token-replay attempts
async def reset_password(request: Request, body: PasswordUpdateRequest):
    """Exchange a recovery access_token for a new password and return a fresh session."""
    if len(body.new_password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")
    try:
        # Set the session using the recovery token so update_user() works
        get_supabase().auth.set_session(body.access_token, body.access_token)
        response = get_supabase().auth.update_user({"password": body.new_password})
    except Exception as exc:
        log.warning("reset_password_failed", extra={"error": str(exc)})
        raise HTTPException(status_code=400, detail="Password reset failed — link may have expired.")

    if not response.user:
        raise HTTPException(status_code=400, detail="Password reset failed — please request a new link.")

    # Fetch a fresh session so the client can log straight in
    session = get_supabase().auth.get_session()
    if session and session.access_token:
        log.info("reset_password_success")
        return TokenResponse(access_token=session.access_token, user_id=str(response.user.id))

    raise HTTPException(status_code=400, detail="Password updated but session could not be established — please sign in.")
