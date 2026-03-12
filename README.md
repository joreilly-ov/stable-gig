# Home Repair Analyser

A single-service web app: **FastAPI** backend that serves both the API and the frontend HTML. Users authenticate via Supabase (email/password, magic-link, or Google SSO), then submit home repair **videos** or **photos** for a structured AI assessment powered by **Google Gemini**.

**Live:** `https://stable-gig-374485351183.europe-west1.run.app`

---

## Features

| Feature | Detail |
|---------|--------|
| **Video analysis** | Upload a video → Gemini 2.5 Flash returns problem type, urgency, materials, clarifying questions, and extracted video metadata (GPS, device, resolution) |
| **Photo analysis** | Upload 1–5 photos → Gemini 1.5 Flash runs Multi-Perspective Triangulation and returns a diagnosis, urgency score (1–10), required tools, and estimated parts |
| **Auth** | Email + password, magic-link OTP, and Google OAuth via Supabase |
| **Rate limiting** | 5–10 req/min per IP on auth endpoints (slowapi) |
| **Onboarding** | Two-step signup: profile (name, address) + trade interests |
| **Dashboard tabs** | Video Analysis tab and Photo Analysis tab in a single SPA |

---

## Project structure

```
backend/
├── main.py                         # FastAPI app — mounts all routers, serves frontend
├── requirements.txt                # Runtime dependencies
├── requirements-test.txt           # Test-only dependencies
├── Dockerfile                      # Cloud Run container image
├── pytest.ini
├── app/
│   ├── config.py                   # Pydantic settings (reads env vars / .env)
│   ├── database.py                 # Supabase client singletons
│   ├── dependencies.py             # get_current_user / get_optional_user
│   ├── models/schemas.py           # Shared Pydantic models
│   ├── routers/
│   │   ├── analyse.py              # POST /analyse          (video)
│   │   ├── photo_analysis.py       # POST /analyse/photos   (photos)
│   │   ├── auth.py                 # POST /auth/*           (login, register, magic-link)
│   │   ├── profiles.py             # GET/PATCH /me/profile
│   │   ├── user_metadata.py        # GET/PATCH /me/metadata
│   │   └── address.py             # GET /address/zip, /address/autocomplete
│   └── services/
│       ├── gemini.py               # Video → Gemini 2.5 Flash
│       ├── photo_analyzer.py       # Photos → Gemini 1.5 Flash (preprocessing + prompt)
│       ├── video_meta.py           # hachoir + mutagen metadata extraction
│       └── smarty.py              # Smarty address autocomplete / ZIP lookup
├── static/
│   └── index.html                  # Deployed frontend (SPA — login, signup, dashboard)
└── tests/
    ├── conftest.py
    ├── test_photo_analyzer_service.py   # 32 unit tests
    └── test_photo_analysis_router.py    # 30 integration tests

frontend/
└── index.html                      # Local dev copy — keep in sync with backend/static/

scripts/
└── create_asana_tickets.py         # One-shot: file TradePhotoAnalyzer Asana tickets

.env.example                        # Template — copy to backend/.env for local dev
```

---

## Running locally

```bash
cd backend
cp ../.env.example .env
# Edit .env — set GEMINI_API_KEY (and optionally SUPABASE_* keys)

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# App at http://localhost:8000
```

**Required env vars:**

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `SUPABASE_URL` | For auth | Your Supabase project URL |
| `SUPABASE_ANON_KEY` | For auth | Supabase anon (public) key |
| `SUPABASE_SERVICE_KEY` | For admin ops | Supabase service-role key (bypasses RLS) |
| `SMARTY_AUTH_ID` / `SMARTY_AUTH_TOKEN` | No | Address autocomplete (omit to disable) |

---

## API

### `POST /analyse` — video

`multipart/form-data` with a `file` (video) field. Optional `browser_lat` / `browser_lon` form fields supply GPS when the video has no embedded coordinates.

```json
{
  "problem_type": "plumbing",
  "description": "A dripping tap in the kitchen sink…",
  "location_in_home": "kitchen",
  "urgency": "low",
  "materials_involved": ["copper pipe", "tap washer"],
  "clarifying_questions": ["How long has the tap been dripping?"],
  "video_metadata": {
    "duration_seconds": 12.4,
    "resolution": "1920x1080",
    "latitude": 51.5074,
    "longitude": -0.1278,
    "device_make": "Samsung",
    "device_model": "SM-G991B"
  }
}
```

Max upload: **350 MB**. Validated via magic bytes (not just Content-Type).

---

### `POST /analyse/photos` — photos

JSON body:

```json
{
  "images": ["data:image/jpeg;base64,…", "https://example.com/photo.jpg"],
  "description": "Damp patch on ceiling below the bathroom (10–1000 chars)",
  "trade_category": "damp"
}
```

`images`: 1–5 entries, each a base64 data URI or HTTPS URL. Supported formats: JPEG, PNG, WebP.
`trade_category`: optional — one of `plumbing`, `electrical`, `structural`, `damp`, `roofing`, `general`.

```json
{
  "likely_issue": "Rising damp caused by failed DPC at ground level",
  "urgency_score": 7,
  "required_tools": ["damp meter", "cold chisel", "hawk and trowel"],
  "estimated_parts": ["DPC membrane", "sand/cement render"],
  "image_feedback": [
    { "index": 0, "role": "Wide Shot",  "quality": "ok",     "note": null },
    { "index": 1, "role": "Close-up",   "quality": "blurry", "note": "Sharpness score 4.2 — retake if possible" }
  ],
  "token_usage_estimate": { "prompt_tokens": 1820, "completion_tokens": 312, "total_tokens": 2132 }
}
```

Images are preprocessed before Gemini: resized to ≤1200 px, re-encoded as JPEG, and sharpness-checked. Each image is assigned a positional role (Wide Shot → Close-up → Scale/Context → Supplemental A/B).

---

### Auth endpoints (`/auth/*`)

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/auth/config` | Returns public Supabase URL + anon key for client-side OAuth |
| `POST` | `/auth/magic-link` | Send a sign-in link to an email address (5/min) |
| `POST` | `/auth/verify` | Exchange an OTP token for a session (10/min) |
| `POST` | `/auth/register` | Register with email + password (5/min) |
| `POST` | `/auth/login/password` | Sign in with email + password (10/min) |

### Profile / metadata (`/me/*`)

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/me/profile` | Fetch name, address fields |
| `PATCH`| `/me/profile` | Update profile |
| `GET`  | `/me/metadata` | Fetch username, bio, trade interests, setup status |
| `PATCH`| `/me/metadata` | Update metadata |

---

## Tests

62 tests, ~1 s, no API keys or network access needed (Gemini and Supabase are mocked).

```bash
cd backend
pip install -r requirements.txt -r requirements-test.txt
pytest          # all 62 tests
pytest -v       # verbose
```

| File | Tests | Covers |
|------|-------|--------|
| `test_photo_analyzer_service.py` | 32 | Sharpness detection · image loading · preprocessing (size guard, resize, blur flag, role assignment) · `analyse()` orchestrator |
| `test_photo_analysis_router.py`  | 30 | Request validation · error→HTTP status mapping · happy-path response shape |

---

## Deploying to Cloud Run

**Project:** `gen-lang-client-0428658103` · **Region:** `europe-west1` · **Service:** `stable-gig`

Secrets (`GEMINI_API_KEY`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`) are stored in GCP Secret Manager and mounted at runtime.

### Build + deploy

```bash
gcloud builds submit backend/ \
  --tag gcr.io/gen-lang-client-0428658103/stable-gig \
  --project=gen-lang-client-0428658103 && \
gcloud run deploy stable-gig \
  --image gcr.io/gen-lang-client-0428658103/stable-gig \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars SUPABASE_URL=https://szpgcvfemllcsajryyuv.supabase.co \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest \
  --project=gen-lang-client-0428658103
```

### First-time secrets setup

```bash
echo -n "YOUR_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=- --project=gen-lang-client-0428658103
echo -n "YOUR_KEY" | gcloud secrets create SUPABASE_ANON_KEY --data-file=- --project=gen-lang-client-0428658103
echo -n "YOUR_KEY" | gcloud secrets create SUPABASE_SERVICE_KEY --data-file=- --project=gen-lang-client-0428658103
```

To rotate a secret: `echo -n "NEW_KEY" | gcloud secrets versions add SECRET_NAME --data-file=- --project=gen-lang-client-0428658103`, then redeploy.

---

## Frontend notes

- `backend/static/index.html` and `frontend/index.html` are identical. Edit one, copy to the other.
- The SPA handles `/`, `/login`, `/signup`, and `/dashboard` — all served by FastAPI.
- Auth tokens are stored in `sessionStorage` (cleared on tab close).
- The dashboard has two tabs: **Video Analysis** and **Photo Analysis**. Tab state is client-side only (no URL change).
