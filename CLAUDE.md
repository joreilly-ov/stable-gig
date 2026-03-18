# CLAUDE.md

## Project overview

**stable-gig** — a two-sided marketplace for home-repair tradesmen. FastAPI backend that also serves the frontend HTML. Homeowners get AI assessments of repair jobs (video or photo), post jobs, hire contractors, and leave verified escrow-gated reviews. Review text is summarised by Claude AI into a Pros/Cons list displayed on the contractor's profile.

## Running locally

```bash
cd backend
pip install -r requirements.txt
# Requires GEMINI_API_KEY (and optionally SUPABASE_* keys) in .env or environment
uvicorn main:app --reload --port 8000
# App is at http://localhost:8000
```

## Key files

### FastAPI backend

| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI app — `GET /` (UI), `POST /analyse`, `POST /analyse/photos` |
| `backend/requirements.txt` | Python runtime dependencies |
| `backend/requirements-test.txt` | Test-only dependencies (pytest, pytest-asyncio) |
| `backend/Dockerfile` | Container image for Cloud Run |
| `backend/app/routers/photo_analysis.py` | `POST /analyse/photos` endpoint |
| `backend/app/services/photo_analyzer.py` | Image load, preprocess, sharpness check, Gemini 1.5 Flash |
| `backend/tests/conftest.py` | Shared test fixtures + module stubs |
| `backend/tests/test_photo_analyzer_service.py` | 32 unit tests for the photo analyzer service |
| `backend/tests/test_photo_analysis_router.py` | 30 integration tests for the photo analysis endpoint |

### Frontend

| File | Purpose |
|------|---------|
| `backend/static/index.html` | **Deployed** frontend SPA, served by FastAPI |
| `frontend/index.html` | Local dev copy — keep in sync with `backend/static/index.html` |
| `frontend/components/ReviewMediator.js` | Vanilla JS: escrow-gated review flow, categorical star ratings, AI Pros/Cons reveal |
| `frontend/components/TradesmanRating.jsx` | React: 5-star form (Quality/Communication/Cleanliness), private feedback field, escrow logic |
| `backend/static/components/ReviewMediator.js` | Deployed copy — keep in sync with `frontend/components/` counterpart |
| `backend/static/components/TradesmanRating.jsx` | Deployed copy — keep in sync with `frontend/components/` counterpart |

### Supabase Edge Functions

| File | Purpose |
|------|---------|
| `supabase/functions/analyse/index.ts` | Gemini 2.0 Flash video analysis (upload → poll → generate) |
| `supabase/functions/contractors/index.ts` | Contractor CRUD — register, fetch, update, `contractor_details` upsert |
| `supabase/functions/review-sentiment/index.ts` | Claude Haiku: extract Pros/Cons from review body; refresh `contractor_details.ai_review_summary` |

### Database migrations

| File | Key additions |
|------|--------------|
| `backend/supabase/migrations/001_initial_schema.sql` | `profiles`, auth trigger |
| `backend/supabase/migrations/002_user_metadata.sql` | `user_metadata` |
| `backend/supabase/migrations/003_contractor_onboarding.sql` | `jobs`, `contractors`, `bids` |
| `backend/supabase/migrations/004_clean_split.sql` | `contractor_details`, Clean Split identity design |
| `backend/supabase/migrations/005_rating_system.sql` | `reviews`, double-blind trigger, `visible_reviews` view, `contractor_rating()` / `client_rating()` helpers |
| `backend/supabase/migrations/006_categorical_ratings.sql` | `jobs.escrow_status`; sub-ratings (Cleanliness · Communication · Accuracy); `reviews.ai_pros_cons`; `contractor_details.ai_review_summary` |
| `backend/supabase/migrations/007_quality_rating_private_feedback.sql` | Renames `rating_accuracy → rating_quality`; adds `reviews.private_feedback` (admin-only, excluded from `visible_reviews`) |

### Other

| File | Purpose |
|------|---------|
| `.env.example` | Template for all required env vars |
| `docs/CustomerReviews.md` | Full review/rating system reference (schema, RLS, components, AI flow) |
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
See `tests/conftest.py` for the stubbing strategy and the reason for the `sys.modules` pre-population.

## Architecture notes

- **Single service on Cloud Run**: FastAPI serves both the API and the UI from one Docker container.
- **Supabase** handles Postgres + RLS, Auth (email / magic-link / Google OAuth), and Edge Functions (Deno/TypeScript).
- **GEMINI_API_KEY** + Supabase keys stored in GCP Secret Manager, mounted into Cloud Run at runtime.
- **ANTHROPIC_API_KEY** stored as a Supabase Edge Function secret — only used by `review-sentiment`, not Cloud Run.
- **Frontend duplication**: `frontend/` ↔ `backend/static/` are kept manually in sync. Edit one, copy to the other (applies to `index.html` and both component files).
- **Clean Split**: `contractors.id = contractor_details.id = auth.users.id`. No separate `user_id` FK on `contractors`.
- **Review system**: double-blind, transaction-anchored (`job_id`), escrow-gated (`jobs.escrow_status = 'funds_released'`). Sub-ratings: Quality · Communication · Cleanliness. Overall `rating` is a Postgres `GENERATED ALWAYS` column (avg of three sub-ratings). `private_feedback` is excluded from `visible_reviews` — admin access via service role only. Claude Haiku summarises each review body into a Pros/Cons list via the `review-sentiment` Edge Function.
- **Video metadata**: extracted locally before uploading to Gemini using `hachoir` (technical metadata) and `mutagen` (embedded MP4 tags). Both fail silently — best-effort only.

## Deploying to Cloud Run (manual)

- **Project:** `gen-lang-client-0428658103`
- **Region:** `europe-west1`
- **Service:** `stable-gig`

### First-time setup

```bash
# 1. Store secrets in Secret Manager
echo -n "YOUR_GEMINI_API_KEY" | \
  gcloud secrets create GEMINI_API_KEY \
    --data-file=- --project=gen-lang-client-0428658103

echo -n "YOUR_SUPABASE_ANON_KEY" | \
  gcloud secrets create SUPABASE_ANON_KEY \
    --data-file=- --project=gen-lang-client-0428658103

echo -n "YOUR_SUPABASE_SERVICE_KEY" | \
  gcloud secrets create SUPABASE_SERVICE_KEY \
    --data-file=- --project=gen-lang-client-0428658103

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

### Deploying Edge Functions

```bash
supabase functions deploy analyse
supabase functions deploy contractors
supabase functions deploy review-sentiment
```

Edge Function secrets (Supabase Dashboard → Project Settings → Edge Functions → Secrets):

```
GEMINI_API_KEY
ANTHROPIC_API_KEY     ← required for review-sentiment
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_KEY
```

### Rotating a secret

```bash
echo -n "NEW_KEY" | gcloud secrets versions add GEMINI_API_KEY \
  --data-file=- --project=gen-lang-client-0428658103
# Then redeploy so Cloud Run picks up the new version.
```
