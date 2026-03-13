"""POST /analyse — video upload and Gemini analysis.

Auth is optional: authenticated users get results persisted to the videos table.
Unauthenticated requests work exactly as before and are not stored.
"""

import asyncio
import logging
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.dependencies import get_optional_user
from app.services import gemini, video_meta as vm

router = APIRouter(tags=["analyse"])
log = logging.getLogger(__name__)

# [SECURITY: code-review] Enforce a hard upload size limit to prevent DoS / OOM.
# 350 MB is generous for a home repair clip while still protecting the container.
_MAX_UPLOAD_BYTES = 350 * 1024 * 1024  # 350 MB
_CHUNK_SIZE = 1024 * 1024              # stream in 1 MB chunks


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


@router.post("/analyse")
async def analyse_video(
    file: UploadFile = File(...),
    browser_lat: float | None = Form(default=None),
    browser_lon: float | None = Form(default=None),
    user=Depends(get_optional_user),
):
    # [SECURITY: code-review] Reject non-video Content-Type (first, cheap check).
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a video")

    suffix = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"

    # [SECURITY: code-review] Stream the upload in chunks so we can enforce the
    # 350 MB limit without reading the entire file into memory first.
    chunks: list[bytes] = []
    total_bytes = 0
    first_chunk = True
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > _MAX_UPLOAD_BYTES:
            limit_mb = _MAX_UPLOAD_BYTES // (1024 * 1024)
            log.warning(
                "upload_too_large",
                extra={"filename": file.filename, "bytes_received": total_bytes},
            )
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {limit_mb} MB upload limit. Please trim the video and try again.",
            )
        # [SECURITY: code-review] Validate actual file magic bytes on the first chunk,
        # not just the client-supplied Content-Type header which is trivially spoofed.
        if first_chunk:
            _assert_video_magic(chunk[:12])
            first_chunk = False
        chunks.append(chunk)

    content = b"".join(chunks)

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
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

        # [SECURITY: code-review] Run the synchronous Gemini SDK in a thread pool so
        # time.sleep inside gemini.analyse() does not block the event loop.
        result = await asyncio.to_thread(gemini.analyse, tmp_path, file.content_type)
        result["video_metadata"] = metadata

        token_usage = result.pop("_token_usage", {})

        log.info("analysis_complete", extra={"user_id": user_id, "filename": file.filename})

        from app.services.usage_logger import log_usage
        log_usage(
            analysis_type="video",
            model="gemini-2.5-flash",
            user_id=user_id,
            prompt_tokens=token_usage.get("prompt_tokens", 0),
            completion_tokens=token_usage.get("completion_tokens", 0),
            total_tokens=token_usage.get("total_tokens", 0),
        )

        # Persist when authenticated
        if user is not None:
            _store_result(user.id, file.filename or "upload", result)

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
        log.error("analyse_failed", extra={"user_id": user_id, "filename": file.filename, "error": msg})
        raise HTTPException(
            status_code=500,
            detail="Analysis failed. Please try again or contact support if the problem persists.",
        )
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
