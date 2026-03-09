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
| `backend/main.py` | FastAPI app — `GET /` (UI) and `POST /analyse` |
| `backend/requirements.txt` | Python dependencies |
| `backend/Dockerfile` | Container image for Cloud Run |
| `backend/static/index.html` | **Deployed** frontend, served by FastAPI |
| `frontend/index.html` | Local dev copy — keep in sync with `backend/static/index.html` |
| `.env.example` | Template for `GEMINI_API_KEY` |

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
# 1. Store the Gemini API key in Secret Manager
echo -n "YOUR_GEMINI_API_KEY" | \
  gcloud secrets create GEMINI_API_KEY \
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
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest \
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
