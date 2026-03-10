"""POST /analyse — video upload and Gemini analysis.

Auth is optional: authenticated users get results persisted to the videos table.
Unauthenticated requests work exactly as before and are not stored.
"""

import logging
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.dependencies import get_optional_user
from app.services import gemini, video_meta as vm

router = APIRouter(tags=["analyse"])
log = logging.getLogger(__name__)


@router.post("/analyse")
async def analyse_video(
    file: UploadFile = File(...),
    browser_lat: float | None = Form(default=None),
    browser_lon: float | None = Form(default=None),
    user=Depends(get_optional_user),
):
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a video")

    suffix = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    user_id = str(user.id) if user else None

    try:
        metadata = vm.extract_video_metadata(tmp_path)

        # Fall back to browser-supplied coords if the video has no embedded GPS
        if browser_lat is not None and browser_lon is not None:
            if "latitude" not in metadata:
                metadata["latitude"] = browser_lat
                metadata["longitude"] = browser_lon
                metadata["location_source"] = "browser"

        result = gemini.analyse(tmp_path, file.content_type)
        result["video_metadata"] = metadata

        log.info("analysis_complete", extra={"user_id": user_id, "filename": file.filename})

        # Persist when authenticated
        if user is not None:
            _store_result(user.id, file.filename or "upload", result)

        return result

    except ValueError as exc:
        log.warning("gemini_non_json", extra={"user_id": user_id, "error": str(exc)})
        raise HTTPException(status_code=422, detail=f"Gemini returned non-JSON: {exc}")
    except Exception as exc:
        msg = str(exc)
        if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower():
            log.error("gemini_quota_exceeded", extra={"user_id": user_id, "error": msg})
            raise HTTPException(
                status_code=429,
                detail="Gemini API quota exceeded. Check billing at https://aistudio.google.com/",
            )
        log.error("analyse_failed", extra={"user_id": user_id, "filename": file.filename, "error": msg})
        raise HTTPException(status_code=500, detail=msg)
    finally:
        os.unlink(tmp_path)


def _store_result(user_id: str, filename: str, result: dict) -> None:
    """Write analysis result to the videos table. Fails silently to avoid blocking the response."""
    try:
        from app.database import get_supabase

        get_supabase().table("videos").insert(
            {"user_id": user_id, "filename": filename, "analysis_result": result}
        ).execute()
    except Exception as exc:
        log.warning("store_result_failed", extra={"user_id": user_id, "error": str(exc)})
