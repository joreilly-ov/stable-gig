import os
import re
import json
import time
import tempfile
import google.generativeai as genai
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set in environment")

genai.configure(api_key=GEMINI_API_KEY)

app = FastAPI(title="Home Repair Video Analyser")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/")
def serve_frontend():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

def extract_video_metadata(file_path: str) -> dict:
    """Extract technical and embedded metadata from a video file."""
    meta = {}

    # --- Technical metadata via hachoir (resolution, duration, frame rate) ---
    try:
        from hachoir.parser import createParser
        from hachoir.metadata import extractMetadata

        parser = createParser(file_path)
        if parser:
            with parser:
                hm = extractMetadata(parser)
            if hm:
                if hm.has("duration"):
                    meta["duration_seconds"] = round(hm.get("duration").total_seconds(), 1)
                if hm.has("width") and hm.has("height"):
                    meta["resolution"] = f"{hm.get('width')}x{hm.get('height')}"
                if hm.has("frame_rate"):
                    try:
                        meta["frame_rate_fps"] = round(float(str(hm.get("frame_rate"))), 2)
                    except (ValueError, TypeError):
                        pass
                if hm.has("creation_date"):
                    meta["recorded_at"] = str(hm.get("creation_date"))
    except Exception:
        pass

    # --- Embedded tags via mutagen (GPS, device make/model, creation date) ---
    try:
        from mutagen.mp4 import MP4

        tags = MP4(file_path)

        if tags.info and "duration_seconds" not in meta:
            meta["duration_seconds"] = round(tags.info.length, 1)

        # GPS: Android/Samsung stores coordinates in the ©xyz atom
        # Format: "+lat+lon/" or "+lat-lon/" e.g. "+51.5074-000.1278/"
        gps_raw = tags.get("\xa9xyz")  # © = \xa9
        if gps_raw:
            gps_str = str(gps_raw[0])
            meta["gps_raw"] = gps_str
            m = re.match(r"([+-]?\d+\.\d+)([+-]\d+\.\d+)", gps_str)
            if m:
                meta["latitude"] = float(m.group(1))
                meta["longitude"] = float(m.group(2))

        # Creation date
        day = tags.get("\xa9day")
        if day and "recorded_at" not in meta:
            meta["recorded_at"] = str(day[0])

        # Device make and model (Samsung stores these in ©mak / ©mod)
        mak = tags.get("\xa9mak")
        if mak:
            meta["device_make"] = str(mak[0])

        mod = tags.get("\xa9mod")
        if mod:
            meta["device_model"] = str(mod[0])

    except Exception:
        pass

    return meta


ANALYSIS_PROMPT = """You are a home repair assessment assistant. Analyse this video and extract the following as JSON:
- problem_type: (e.g. plumbing, electrical, structural, damp, general)
- description: a plain English summary of the issue visible
- location_in_home: best guess at where this is (e.g. bathroom, kitchen, external wall)
- urgency: low / medium / high / emergency
- materials_involved: list of materials or components visible
- clarifying_questions: list of 2-3 questions a tradesperson would want answered before quoting

Return only valid JSON, no markdown."""


@app.post("/analyse")
async def analyse_video(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a video")

    suffix = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        video_metadata = extract_video_metadata(tmp_path)

        uploaded = genai.upload_file(tmp_path, mime_type=file.content_type)

        # Wait for file to become ACTIVE before using it
        while uploaded.state.name == "PROCESSING":
            time.sleep(2)
            uploaded = genai.get_file(uploaded.name)

        if uploaded.state.name != "ACTIVE":
            raise RuntimeError(f"Uploaded file entered state {uploaded.state.name!r}")

        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content([ANALYSIS_PROMPT, uploaded])

        raw_text = response.text.strip()
        # Strip markdown code fences that Gemini occasionally adds
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text).strip()
        result = json.loads(raw_text)
        result["video_metadata"] = video_metadata
        return result
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=422,
            detail=f"Gemini returned non-JSON response: {raw_text[:300]}",
        )
    except Exception as exc:
        msg = str(exc)
        if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower():
            raise HTTPException(
                status_code=429,
                detail="Gemini API quota exceeded. Please check your billing plan at https://aistudio.google.com/",
            )
        raise HTTPException(status_code=500, detail=msg)
    finally:
        os.unlink(tmp_path)
