import os
import json
import tempfile
import google.generativeai as genai
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
        uploaded = genai.upload_file(tmp_path, mime_type=file.content_type)

        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content([ANALYSIS_PROMPT, uploaded])

        raw_text = response.text.strip()
        result = json.loads(raw_text)
        return result
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail=f"Gemini returned non-JSON response: {raw_text[:300]}",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        os.unlink(tmp_path)
