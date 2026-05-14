"""POST /analyse — video upload and Gemini analysis.

Auth is optional: authenticated users get results persisted to the videos table.
Unauthenticated requests work exactly as before and are not stored.
"""

import asyncio
import base64
import logging
import os
import tempfile

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile

# [SECURITY: code-review] Shared rate limiter — same instance used by auth endpoints.
from main import limiter

from app.dependencies import get_optional_user
from app.services import gemini, photo_analyzer, video_meta as vm

router = APIRouter(tags=["analyse"])
log = logging.getLogger(__name__)

# [SECURITY: code-review] Enforce a hard upload size limit to prevent DoS / OOM.
# 350 MB is generous for a home repair clip while still protecting the container.
_MAX_UPLOAD_BYTES = 350 * 1024 * 1024  # 350 MB
_MAX_IMAGE_BYTES  =  20 * 1024 * 1024  # 20 MB — consistent with /analyse/photos
_CHUNK_SIZE = 1024 * 1024              # stream in 1 MB chunks

_SUPPORTED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


# [SECURITY: code-review] Magic-byte signatures for common video containers.
# Content-Type is client-supplied and trivially spoofed; this validates the
# actual file payload before it is sent to Gemini.
def _assert_video_magic(header: bytes) -> None:
    """Raise HTTP 400 if *header* (first ≥12 bytes) is not a recognised video format."""
    if len(header) < 8:
        raise HTTPException(status_code=400, detail="Uploaded file is too small to be a valid video")
    # MP4 / MOV / 3GP — ISO base media file format: 'ftyp' box at offset 4
    if header[4:8] == b"ftyp":
        return
    # WebM / Matroska
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return
    # AVI (RIFF container)
    if header[:4] == b"RIFF" and len(header) >= 12 and header[8:11] == b"AVI":
        return
    # MPEG Program Stream / MPEG Elementary Stream
    if header[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return
    # MPEG-TS (188-byte packets, sync byte 0x47)
    if header[0] == 0x47:
        return
    raise HTTPException(status_code=400, detail="Uploaded file does not appear to be a valid video")


def _assert_image_magic(header: bytes) -> None:
    """Raise HTTP 400 if *header* (first ≥12 bytes) is not a recognised image format."""
    if len(header) < 4:
        raise HTTPException(status_code=400, detail="Uploaded file is too small to be a valid image")
    # JPEG: FF D8 FF
    if header[:3] == b"\xff\xd8\xff":
        return
    # PNG: 8-byte signature
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return
    # WebP: RIFF....WEBP
    if header[:4] == b"RIFF" and len(header) >= 12 and header[8:12] == b"WEBP":
        return
    raise HTTPException(status_code=400, detail="Uploaded file does not appear to be a valid image (JPEG, PNG, or WebP)")


@router.post("/analyse")
# [SECURITY: code-review] 5 req/min per IP limits Gemini quota burn from the
# intentionally-unauthenticated public demo endpoint.
@limiter.limit("5/minute")
async def analyse_video(
    request: Request,
    file: UploadFile = File(...),
    browser_lat: float | None = Form(default=None),
    browser_lon: float | None = Form(default=None),
    description: str | None = Form(default=None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    user=Depends(get_optional_user),
):
    is_image = file.content_type in _SUPPORTED_IMAGE_TYPES
    is_video = bool(file.content_type and file.content_type.startswith("video/"))

    # [SECURITY: code-review] Reject non-video, non-image Content-Type (first, cheap check).
    if not is_image and not is_video:
        raise HTTPException(status_code=400, detail="Uploaded file must be a video or an image (JPEG, PNG, WebP)")

    suffix = os.path.splitext(file.filename or ("photo.jpg" if is_image else "video.mp4"))[1] or (".jpg" if is_image else ".mp4")
    max_bytes = _MAX_IMAGE_BYTES if is_image else _MAX_UPLOAD_BYTES

    # [SECURITY: code-review] Stream the upload in chunks so we can enforce the
    # size limit without reading the entire file into memory first.
    chunks: list[bytes] = []
    total_bytes = 0
    first_chunk = True
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            limit_mb = max_bytes // (1024 * 1024)
            log.warning(
                "upload_too_large",
                extra={"upload_filename": file.filename, "bytes_received": total_bytes},
            )
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {limit_mb} MB upload limit. Please trim the video and try again.",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        # [SECURITY: code-review] Validate actual file magic bytes on the first chunk,
        # not just the client-supplied Content-Type header which is trivially spoofed.
        if first_chunk:
            if is_image:
                _assert_image_magic(chunk[:12])
            else:
                _assert_video_magic(chunk[:12])
            first_chunk = False
        chunks.append(chunk)

    content = b"".join(chunks)
    user_id = str(user.id) if user else None

    # --- Image branch ----------------------------------------------------
    if is_image:
        b64 = base64.b64encode(content).decode()
        data_uri = f"data:{file.content_type};base64,{b64}"
        desc = (
            description
            if description and len(description.strip()) >= 10
            else "Please analyse this uploaded image for home repair issues"
        )

        try:
            result = await photo_analyzer.analyse(
                images=[data_uri],
                description=desc,
                trade_category=None,
            )
        except ValueError as exc:
            log.warning("image_analysis_bad_input", extra={"user_id": user_id, "error": str(exc)})
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "quota" in msg.lower() or "rate limit" in msg.lower() or "ratelimit" in msg.lower():
                log.error("image_gemini_quota", extra={"user_id": user_id, "error": msg})
                raise HTTPException(
                    status_code=429,
                    detail="Gemini API quota exceeded. Check billing at https://aistudio.google.com/",
                )
            log.error("image_analysis_failed", extra={"user_id": user_id, "error": msg})
            raise HTTPException(
                status_code=500,
                detail="Analysis failed. Please try again or contact support if the problem persists.",
            )

        token_usage = result.get("token_usage_estimate", {})
        from app.services.usage_logger import log_usage
        log_usage(
            analysis_type="photo",
            model="gemini-2.5-flash",
            user_id=user_id,
            prompt_tokens=token_usage.get("prompt_tokens", 0),
            completion_tokens=token_usage.get("completion_tokens", 0),
            total_tokens=token_usage.get("total_tokens", 0),
        )

        log.info("image_analysis_complete", extra={"user_id": user_id, "upload_filename": file.filename})

        if user is not None:
            result["job_id"] = _create_draft_job(user_id, desc, result)

        return result

    # --- Video branch (original flow) ------------------------------------
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        metadata = vm.extract_video_metadata(tmp_path)

        # Fall back to browser-supplied coords if the video has no embedded GPS
        if browser_lat is not None and browser_lon is not None:
            if "latitude" not in metadata:
                metadata["latitude"] = browser_lat
                metadata["longitude"] = browser_lon
                metadata["location_source"] = "browser"

        # [SECURITY: code-review] Run the synchronous Gemini SDK in a thread pool so
        # time.sleep inside gemini.analyse() does not block the event loop.
        result = await asyncio.to_thread(gemini.analyse, tmp_path, file.content_type)
        result["video_metadata"] = metadata

        token_usage = result.pop("_token_usage", {})

        log.info("analysis_complete", extra={"user_id": user_id, "upload_filename": file.filename})

        from app.services.usage_logger import log_usage
        log_usage(
            analysis_type="video",
            model="gemini-2.5-flash",
            user_id=user_id,
            prompt_tokens=token_usage.get("prompt_tokens", 0),
            completion_tokens=token_usage.get("completion_tokens", 0),
            total_tokens=token_usage.get("total_tokens", 0),
        )

        # Persist when authenticated — create a draft job synchronously so job_id
        # is available in the response, then store to the videos table in the background.
        if user is not None:
            job_desc = result.get("description") or "Video analysis"
            result["job_id"] = _create_draft_job(str(user.id), job_desc, result)
            background_tasks.add_task(
                _store_result, str(user.id), file.filename or "upload", result
            )

        return result

    except ValueError as exc:
        log.warning("gemini_non_json", extra={"user_id": user_id, "error": str(exc)})
        raise HTTPException(status_code=422, detail=f"Gemini returned non-JSON: {exc}")
    except HTTPException:
        raise  # re-raise 413 / 400 from size / magic checks above
    except Exception as exc:
        msg = str(exc)
        if "429" in msg or "quota" in msg.lower() or "rate limit" in msg.lower() or "ratelimit" in msg.lower():
            log.error("gemini_quota_exceeded", extra={"user_id": user_id, "error": msg})
            raise HTTPException(
                status_code=429,
                detail="Gemini API quota exceeded. Check billing at https://aistudio.google.com/",
            )
        # [SECURITY: code-review] Do not leak internal error details to the caller;
        # log the full message server-side and return a generic response.
        log.error("analyse_failed", extra={"user_id": user_id, "upload_filename": file.filename, "error": msg})
        raise HTTPException(
            status_code=500,
            detail="Analysis failed. Please try again or contact support if the problem persists.",
        )
    finally:
        os.unlink(tmp_path)


def _create_draft_job(user_id: str, description: str, analysis_result: dict) -> str | None:
    """Insert a draft job row and return its UUID. Returns None on any failure so callers degrade gracefully."""
    try:
        from app.database import get_supabase_admin
        res = get_supabase_admin().table("jobs").insert({
            "user_id":         user_id,
            "title":           description[:200],
            "description":     description,
            "activity":        "general",
            "postcode":        "TBC",
            "status":          "draft",
            "analysis_result": analysis_result,
        }).execute()
        if res.data:
            job_id = res.data[0]["id"]
            log.info("draft_job_created", extra={"user_id": user_id, "job_id": job_id})
            return job_id
        log.warning("draft_job_no_data", extra={"user_id": user_id})
    except Exception as exc:
        log.warning("draft_job_failed", extra={"user_id": user_id, "error": str(exc)})
    return None


def _store_result(user_id: str, filename: str, result: dict) -> None:
    """Persist analysis result to the videos table.

    Must use the service-role client: the anon client has no session so
    auth.uid() returns NULL and the 'videos: insert own' RLS policy blocks
    every write.  Service role bypasses RLS and can write on behalf of the
    authenticated user identified by user_id.

    Called via BackgroundTasks so it runs after the response is sent.
    Failures are logged but never surface to the caller.
    """
    try:
        from app.database import get_supabase_admin

        get_supabase_admin().table("videos").insert(
            {"user_id": user_id, "filename": filename, "analysis_result": result}
        ).execute()
        log.info("store_result_ok", extra={"user_id": user_id, "filename": filename})
    except Exception as exc:
        log.warning("store_result_failed", extra={"user_id": user_id, "error": str(exc)})
