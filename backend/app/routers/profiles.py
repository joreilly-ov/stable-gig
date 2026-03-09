"""User profile routes — /me/profile."""

from fastapi import APIRouter, Depends, HTTPException

from app.database import get_supabase
from app.dependencies import get_current_user
from app.models.schemas import ProfileResponse, ProfileUpdate

router = APIRouter(prefix="/me", tags=["profiles"])


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(user=Depends(get_current_user)):
    result = (
        get_supabase()
        .table("profiles")
        .select("*")
        .eq("id", str(user.id))
        .maybe_single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Profile not found")
    return result.data


@router.patch("/profile", response_model=ProfileResponse)
async def update_profile(body: ProfileUpdate, user=Depends(get_current_user)):
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=422, detail="No fields provided to update")

    result = (
        get_supabase()
        .table("profiles")
        .update(data)
        .eq("id", str(user.id))
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Profile not found or not owned by this user")
    return result.data[0]
