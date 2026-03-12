# CLAUDE.md

## Project overview

Single-service web app: FastAPI backend that also serves the frontend HTML. Gemini 2.0 Flash analyses uploaded home repair videos and returns a structured JSON assessment.

## Running locally

```bash
cd backend
pip install -r requirements.txt
# Requires GEMINI_API_KEY in .env or environment
uvicorn main:app --reload --port 8000
# App is at http://localhost:8000
```

## Key files

| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI app — `GET /` (UI), `POST /analyse`, `POST /analyse/photos` |
| `backend/requirements.txt` | Python dependencies |
| `backend/requirements-test.txt` | Test-only dependencies (pytest, pytest-asyncio) |
| `backend/Dockerfile` | Container image for Cloud Run |
| `backend/static/index.html` | **Deployed** frontend, served by FastAPI |
| `frontend/index.html` | Local dev copy — keep in sync with `backend/static/index.html` |
| `.env.example` | Template for `GEMINI_API_KEY` |
| `backend/app/routers/photo_analysis.py` | TradePhotoAnalyzer endpoint — `POST /analyse/photos` |
| `backend/app/services/photo_analyzer.py` | Image load, preprocess, sharpness check, Gemini 1.5 Flash call |
| `backend/tests/conftest.py` | Shared test fixtures + module stubs |
| `backend/tests/test_photo_analyzer_service.py` | Unit tests for the photo analyzer service |
| `backend/tests/test_photo_analysis_router.py` | Integration tests for the photo analysis endpoint |
| `scripts/create_asana_tickets.py` | One-shot script to file TradePhotoAnalyzer Asana tickets |

## Running the tests

```bash
cd backend
pip install -r requirements.txt -r requirements-test.txt
pytest            # 62 tests, ~1 s, no API keys needed
pytest -v         # verbose output
```

**Test layout:**

| File | Count | Covers |
|------|-------|--------|
| `tests/test_photo_analyzer_service.py` | 32 | Sharpness detection · image loading · preprocessing pipeline (size guard, resize, blur flag, role assignment) · `analyse()` orchestrator |
| `tests/test_photo_analysis_router.py` | 30 | Request validation · error→HTTP status mapping · happy-path response shape |

Gemini and Supabase are never called — all external dependencies are mocked.
See `tests/conftest.py` for the stubbing strategy and the reason for the
`sys.modules` pre-population.

## Architecture notes

- **Single service on Cloud Run**: FastAPI serves both the API and the UI from a Docker container.
- **GEMINI_API_KEY** is stored in GCP Secret Manager and mounted into Cloud Run at runtime.
- **Frontend duplication**: `frontend/index.html` and `backend/static/index.html` are identical. Edit one, copy to the other.
- **Video metadata**: Extracted locally before uploading to Gemini using `hachoir` (technical metadata) and `mutagen` (embedded MP4 tags). Both libraries fail silently — metadata is best-effort.
- **No tests**: This is a proof-of-concept. There is no test suite.

## Deploying to Cloud Run (manual)

- **Project:** `gen-lang-client-0428658103`
- **Region:** `europe-west1`
- **Service:** `stable-gig`

### First-time setup

```bash
# 1. Store secrets in Secret Manager
echo -n "YOUR_GEMINI_API_KEY" | \
  gcloud secrets create GEMINI_API_KEY \
    --data-file=- \
    --project=gen-lang-client-0428658103

echo -n "YOUR_SUPABASE_ANON_KEY" | \
  gcloud secrets create SUPABASE_ANON_KEY \
    --data-file=- \
    --project=gen-lang-client-0428658103

echo -n "YOUR_SUPABASE_SERVICE_KEY" | \
  gcloud secrets create SUPABASE_SERVICE_KEY \
    --data-file=- \
    --project=gen-lang-client-0428658103

# 2. Build and push the container image
gcloud builds submit backend/ \
  --tag gcr.io/gen-lang-client-0428658103/stable-gig \
  --project=gen-lang-client-0428658103

# 3. Deploy to Cloud Run
gcloud run deploy stable-gig \
  --image gcr.io/gen-lang-client-0428658103/stable-gig \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars SUPABASE_URL=https://szpgcvfemllcsajryyuv.supabase.co \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest \
  --project=gen-lang-client-0428658103
```

### Subsequent deploys (after code changes)

```bash
gcloud builds submit backend/ \
  --tag gcr.io/gen-lang-client-0428658103/stable-gig \
  --project=gen-lang-client-0428658103 && \
gcloud run deploy stable-gig \
  --image gcr.io/gen-lang-client-0428658103/stable-gig \
  --platform managed \
  --region europe-west1 \
  --set-env-vars SUPABASE_URL=https://szpgcvfemllcsajryyuv.supabase.co \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest \
  --project=gen-lang-client-0428658103
```

### Updating the secret

```bash
echo -n "NEW_KEY" | \
  gcloud secrets versions add GEMINI_API_KEY \
    --data-file=- \
    --project=gen-lang-client-0428658103
# Then redeploy so Cloud Run picks up the new version.
```
