# Home Repair Video Analyser

A minimal proof-of-concept web app that lets you upload a short video of a home repair issue and receive an AI-generated structured assessment powered by **Google Gemini 2.0 Flash**.

## Project structure

```
/backend
  main.py           # FastAPI app — GET / and POST /analyse
  requirements.txt
  /static
    index.html      # Frontend served by FastAPI at GET /
/frontend
  index.html        # Local dev copy (keep in sync with backend/static/)
.env.example
README.md
```

> `backend/static/index.html` is the copy that gets deployed to Railway.
> `frontend/index.html` is kept for local development convenience.
> If you edit one, update the other.

## Prerequisites

- Python 3.11+
- A [Google AI Studio](https://aistudio.google.com/) API key with Gemini access

## Setup

1. **Clone and enter the repo**

   ```bash
   git clone <repo-url>
   cd <repo>
   ```

2. **Create your `.env` file**

   ```bash
   cp .env.example .env
   # Edit .env and set your key:
   # GEMINI_API_KEY=your_key_here
   ```

3. **Install backend dependencies**

   ```bash
   cd backend
   pip install -r requirements.txt
   ```

4. **Run the backend**

   ```bash
   uvicorn main:app --reload --port 8000
   ```

5. **Open the app**

   Visit [http://localhost:8000](http://localhost:8000) — the backend serves the frontend directly.

## API

### `GET /`

Returns the frontend UI (`backend/static/index.html`).

### `POST /analyse`

Accepts a `multipart/form-data` upload with a single field named `file` containing a video.

**Response** (JSON):

```json
{
  "problem_type": "plumbing",
  "description": "A dripping tap in the kitchen sink …",
  "location_in_home": "kitchen",
  "urgency": "low",
  "materials_involved": ["copper pipe", "tap washer"],
  "clarifying_questions": [
    "How long has the tap been dripping?",
    "Is the drip from the hot or cold side?",
    "Have you noticed any water damage under the sink?"
  ],
  "video_metadata": {
    "duration_seconds": 12.4,
    "resolution": "1920x1080",
    "frame_rate_fps": 30.0,
    "recorded_at": "2024-11-01T10:23:00",
    "latitude": 51.5074,
    "longitude": -0.1278,
    "device_make": "Samsung",
    "device_model": "SM-G991B"
  }
}
```

`video_metadata` is a best-effort extraction from the file's technical and embedded tags (via `hachoir` and `mutagen`). Fields are omitted if not present in the file.

## Testing

The project has a unit + integration test suite covering the **TradePhotoAnalyzer** service
(`POST /analyse/photos`).  No API keys or network access are needed — all external services
(Gemini, Supabase) are mocked.

### Test structure

```
backend/
├── tests/
│   ├── conftest.py                      # Shared fixtures + module stubs
│   ├── test_photo_analyzer_service.py   # Unit tests: sharpness, image loading, preprocessing, analyse()
│   └── test_photo_analysis_router.py    # Integration tests: validation, error handling, happy path
├── pytest.ini                           # asyncio_mode = auto, testpaths = tests
└── requirements-test.txt                # pytest + pytest-asyncio
```

### Running the tests

```bash
cd backend
pip install -r requirements.txt -r requirements-test.txt
pytest
```

To see verbose output:

```bash
pytest -v
```

### What is tested

| File | Tests | What they cover |
|------|-------|-----------------|
| `test_photo_analyzer_service.py` | 32 | `_sharpness_score` — blurry vs sharp images<br>`_fetch_image_bytes` — base64 data URIs, bad schemes<br>`_load_and_preprocess` — size guard, resize, blur flag, role assignment, corrupt files<br>`analyse()` — all-bad images, urgency clamping, token usage, feedback shape |
| `test_photo_analysis_router.py` | 30 | Request validation — empty list, >5 images, short/long description, invalid category<br>Error mapping — ValueError→422, quota→429, generic→500, no info leakage in 500s<br>Happy path — correct response shape, urgency clamping, arg passthrough |

### Notes

- Gemini is never called: `_call_gemini` is patched in service tests; `photo_analyzer.analyse`
  is patched in router tests.
- `google.generativeai` and `supabase` are stubbed in `conftest.py` because the
  cryptography C extension panics in the sandbox environment.  In a real environment
  (Cloud Run, your local machine) the real packages work fine and the stubs are not used.

---

## Deploying to Railway

1. Push this repo to GitHub.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Set **Root Directory** to `backend`.
4. Set the start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add the `GEMINI_API_KEY` environment variable in Railway's settings.
6. The app serves the UI at `/` — no separate static hosting needed.
