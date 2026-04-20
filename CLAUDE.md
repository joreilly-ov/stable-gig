# CLAUDE.md

## Project overview

**stable-gig** — a two-sided marketplace for home-repair tradesmen. FastAPI backend that also serves the frontend HTML. Homeowners get AI assessments of repair jobs (video or photo), post jobs, hire contractors, and leave verified escrow-gated reviews. Review text is summarised by Claude AI into a Pros/Cons list displayed on the contractor's profile.

There are **two separate frontends**:
- **FastAPI-served SPA** (`backend/static/index.html`) — the default UI at the Cloud Run URL, served at `/`
- **Lovable PWA** — a separate React app built and hosted in Lovable; installable on mobile (iOS/Android home screen). Talks to the same Cloud Run backend via cross-origin requests (CORS is configured `allow_origins=["*"]` for this reason).

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
| `backend/main.py` | FastAPI app — `GET /` (UI), `POST /analyse`, `POST /analyse/photos`, `/jobs`, `/jobs/{id}/bids` |
| `backend/requirements.txt` | Python runtime dependencies |
| `backend/requirements-test.txt` | Test-only dependencies (pytest, pytest-asyncio) |
| `backend/Dockerfile` | Container image for Cloud Run |
| `backend/app/routers/photo_analysis.py` | `POST /analyse/photos` endpoint |
| `backend/app/services/photo_analyzer.py` | Image load, preprocess, sharpness check, Gemini 1.5 Flash |
| `backend/app/routers/task_breakdown.py` | `POST /analyse/breakdown` endpoint |
| `backend/app/services/task_breakdown.py` | Claude Haiku: decompose repair description into ordered task list |
| `backend/app/routers/jobs.py` | `POST/GET /jobs`, `GET/PATCH /jobs/{id}` — homeowner job lifecycle; fires push notification on publish |
| `backend/app/routers/bids.py` | `POST/GET /jobs/{id}/bids`, `PATCH /jobs/{id}/bids/{bid_id}`, `DELETE /jobs/{id}/bids/{bid_id}`, `GET /me/bids` — contractor bidding; soft-delete on pending bids |
| `backend/app/routers/reviews.py` | `POST /reviews`, `GET /reviews/contractor/{id}`, `GET /reviews/summary/{id}`, `DELETE /reviews/{id}` — review flow; live schema uses `contractor_id`/`comment`/`overall` columns |
| `backend/app/routers/questions.py` | Anonymous contractor Q&A per job; homeowner sees "Contractor N" labels |
| `backend/app/routers/notifications.py` | VAPID public key endpoint; push subscription upsert/delete |
| `backend/app/routers/milestones.py` | Homeowner-defined milestones; contractor photo evidence; homeowner approve/reject |
| `backend/app/services/push_service.py` | `notify_contractors_of_new_job()` — Web Push via pywebpush (RFC 8030 + VAPID) |
| `backend/tests/conftest.py` | Shared test fixtures + module stubs (pywebpush, google.generativeai, supabase) |
| `backend/tests/test_photo_analyzer_service.py` | 32 unit tests for the photo analyzer service |
| `backend/tests/test_photo_analysis_router.py` | 30 integration tests for the photo analysis endpoint |
| `backend/tests/test_reviews_router.py` | 14 tests for the reviews router |
| `backend/tests/test_questions_router.py` | 13 tests for the questions router |
| `backend/tests/test_notifications_router.py` | 8 tests for the notifications router |
| `backend/tests/test_milestones_router.py` | 17 tests for the milestones router |
| `backend/tests/test_push_service.py` | 9 tests for the push notification service |
| `backend/tests/test_contractor_matcher_service.py` | 25 unit tests for the contractor-matching service |
| `backend/tests/test_escrow_service.py` | 35 unit tests for the escrow service |
| `backend/tests/test_rfp_generator_service.py` | 27 unit tests for the RFP generator service |

### Frontend — FastAPI-served SPA

| File | Purpose |
|------|---------|
| `backend/static/index.html` | **Deployed** frontend SPA, served by FastAPI at `/` |
| `frontend/index.html` | Local dev copy — keep in sync with `backend/static/index.html` |
| `frontend/components/ReviewMediator.js` | Vanilla JS: escrow-gated review flow, categorical star ratings, AI Pros/Cons reveal |
| `frontend/components/TradesmanRating.jsx` | React: 5-star form (Quality/Communication/Cleanliness), private feedback field, escrow logic |
| `backend/static/components/ReviewMediator.js` | Deployed copy — keep in sync with `frontend/components/` counterpart |
| `backend/static/components/TradesmanRating.jsx` | Deployed copy — keep in sync with `frontend/components/` counterpart |

### Frontend — Lovable PWA

A separate React app developed and hosted in **Lovable**. Source lives in the Lovable project (not in this repo). Key characteristics:

- **Progressive Web App**: includes a web manifest and service worker; users can "Add to Home Screen" on iOS and Android for a native-app feel
- **Cross-origin**: all API calls target `https://stable-gig-374485351183.europe-west1.run.app` — this is why the backend has `CORSMiddleware(allow_origins=["*"])`
- **Upload limit**: client-side cap is **30 MB** (Cloud Run's GFE load balancer enforces a 32 MB hard limit before requests reach the app; the cap prevents hitting that wall)
- **Auth**: uses `@supabase/supabase-js` with the same Supabase project; the JWT is sent as `Authorization: Bearer <token>` on protected endpoints

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
| `backend/supabase/migrations/008_private_feedback_column_security.sql` | Column-level `REVOKE SELECT (private_feedback)` from `authenticated`/`anon`; re-grants all other columns |
| `backend/supabase/migrations/009_jobs_analysis_result.sql` | `jobs.analysis_result JSONB` — stores Gemini output with the job so it survives page refresh |
| `backend/supabase/migrations/010_bidding_status_expansion.sql` | Expands `jobs.status` to `draft \| open \| awarded \| in_progress \| completed \| cancelled` |
| `backend/supabase/migrations/011_rfp_and_embeddings.sql` | `jobs.rfp_content JSONB`; `contractors.profile_embedding vector(768)` for semantic matching |
| `backend/supabase/migrations/012_escrow_transactions.sql` | `escrow_transactions` audit table |
| `backend/supabase/migrations/013_job_questions.sql` | `job_questions` — anonymous contractor Q&A; RLS: owner sees all, contractor sees own |
| `backend/supabase/migrations/014_push_subscriptions.sql` | `push_subscriptions` — Web Push endpoint/key per user; UNIQUE on `(user_id, endpoint)` |
| `backend/supabase/migrations/015_job_milestones.sql` | `job_milestones` + `milestone_photos` — photo evidence per milestone |
| `backend/supabase/migrations/016_reviews_rls_hardening.sql` | Drops USING(true) SELECT policies; adds narrowly-scoped policies; re-asserts column REVOKE |
| `backend/supabase/migrations/017_reconcile_missing_tables.sql` | Reconciliation migration (safe / idempotent): creates `contractor_details`, `job_questions`, `push_subscriptions`, `job_milestones`, `milestone_photos` with schemas matching the live DB (uses `user_id` FK on `contractors`, `expertise` array); backfills `contractor_details` for existing contractors; adds `on_contractor_created` trigger |

### Other

| File | Purpose |
|------|---------|
| `.env.example` | Template for all required env vars |
| `docs/CustomerReviews.md` | Full review/rating system reference (schema, RLS, components, AI flow) |
| `scripts/create_asana_tickets.py` | One-shot script to file TradePhotoAnalyzer Asana tickets |
| `scripts/seed_data.py` | Populates test data (5 homeowners, 5 contractors, 13 jobs) via Supabase REST; uses `seed_helper.sql` RPC to work around PGRST204 schema-cache issue with `TEXT[]` columns |
| `scripts/seed_helper.sql` | RPC functions (`seed_insert_contractor`, `seed_insert_review`) required by `seed_data.py` — apply once to the target Supabase project |
| `scripts/generate_docs.ps1` | Orchestrates `openapi.json`, `test-inventory.txt`, and `feature-matrix.md` generation; run with `pwsh ./scripts/generate_docs.ps1` |

## Running the tests

```bash
cd backend
pip install -r requirements.txt -r requirements-test.txt
pytest            # 447 tests, ~2 s, no API keys needed
pytest -v         # verbose output
```

**Test layout:**

| File | Count | Covers |
|------|-------|--------|
| `tests/test_photo_analyzer_service.py` | 32 | Sharpness detection · image loading · preprocessing pipeline (size guard, resize, blur flag, role assignment) · `analyse()` orchestrator |
| `tests/test_photo_analysis_router.py` | 30 | Request validation · error→HTTP status mapping · happy-path response shape |
| `tests/test_task_breakdown.py` | 18 | Router error mapping · service validation · prompt content · float coercion · fence stripping |
| `tests/test_jobs_bids_router.py` | 30 | Job CRUD · status transitions · contractor bid placement · accept/reject · auth guards |
| `tests/test_reviews_router.py` | 14 | Submit review · private_feedback stripped · duplicate detection · list + summary endpoints |
| `tests/test_questions_router.py` | 13 | Contractor Q&A · anonymisation · owner answers · auth guards |
| `tests/test_notifications_router.py` | 8 | VAPID key endpoint · subscribe/unsubscribe · VAPID-not-configured 503 |
| `tests/test_milestones_router.py` | 17 | Create milestones · list with photos · contractor photo submit · approve/reject |
| `tests/test_push_service.py` | 9 | VAPID config check · no-contractors skip · send + dead-subscription cleanup |
| `tests/test_contractor_matcher_service.py` | 25 | Profile embedding · job query text · semantic matching · expertise-filter fallback |
| `tests/test_escrow_service.py` | 35 | Payment intent · held state · transfer · refund · status checks |
| `tests/test_rfp_generator_service.py` | 27 | RFP generation · prompt building · cost validation · Gemini call shape |

Gemini, Supabase, and pywebpush are never called — all external dependencies are mocked.
See `tests/conftest.py` for the stubbing strategy and the reason for the `sys.modules` pre-population.

## Architecture notes

- **Single service on Cloud Run**: FastAPI serves both the API and the UI from one Docker container.
- **Two frontends**: the FastAPI-served SPA (`/`) and the Lovable PWA (separate origin). CORS is `allow_origins=["*"]` to support the cross-origin Lovable app.
- **Lovable PWA**: installable on iOS/Android home screen. Upload limit is 30 MB client-side (Cloud Run's GFE enforces a 32 MB hard cap before the app runs; no CORS headers are added to GFE rejections).
- **Supabase** handles Postgres + RLS, Auth (email / magic-link / Google OAuth), and Edge Functions (Deno/TypeScript).
- **GEMINI_API_KEY** + Supabase keys stored in GCP Secret Manager, mounted into Cloud Run at runtime.
- **ANTHROPIC_API_KEY** stored as a Supabase Edge Function secret — only used by `review-sentiment`, not Cloud Run.
- **Frontend duplication**: `frontend/` ↔ `backend/static/` are kept manually in sync. Edit one, copy to the other (applies to `index.html` and both component files).
- **Contractor identity (live DB)**: `contractors` has a separate `user_id UUID` FK referencing `auth.users.id`; the PK `contractors.id` is an auto-generated UUID (not the auth UUID). All Python lookups use `.eq("user_id", user_id)`. `contractor_details.id` references `contractors.id`. Migration 017 and all RLS policies are written against this `user_id` FK model.
- **Review system**: double-blind, transaction-anchored (`job_id`), escrow-gated (`jobs.escrow_status = 'funds_released'`). Sub-ratings: Quality · Communication · Cleanliness. Overall `rating` is a Postgres `GENERATED ALWAYS` column (avg of three sub-ratings). `private_feedback` is excluded from `visible_reviews` AND protected by column-level `REVOKE SELECT` on the raw table — admin access via service role only. Claude Haiku summarises each review body into a Pros/Cons list via the `review-sentiment` Edge Function.
- **Photo analysis auth**: `POST /analyse/photos` requires a valid Supabase JWT (`get_current_user`). The video endpoint `POST /analyse` allows unauthenticated access (public demo).
- **Video metadata**: extracted locally before uploading to Gemini using `hachoir` (technical metadata) and `mutagen` (embedded MP4 tags). Both fail silently — best-effort only.
- **Bidding framework**: jobs start as `draft`, homeowner publishes to `open` (visible to all contractors), contractors submit bids (`amount_pence` + `note`), homeowner accepts one bid (job moves to `awarded`, all other bids rejected), then `in_progress → completed`. All bid endpoints require JWT auth; DB writes use service-role client to bypass RLS with Python-level ownership checks. Contractors can soft-delete their own pending bids (`DELETE /jobs/{id}/bids/{bid_id}`); accepted/rejected bids are immutable.
- **Anonymous Q&A**: contractors ask questions on `open`/`awarded`/`in_progress` jobs; homeowner sees stable "Contractor N" labels; contractor identity is never exposed to the homeowner.
- **Web Push**: when a homeowner publishes a job (`draft → open`), `notify_contractors_of_new_job()` fires as a BackgroundTask, queries contractors with matching `expertise` (trade categories), fetches their `push_subscriptions`, and sends VAPID-signed push notifications. Requires `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_CLAIMS_EMAIL` in environment. Dead subscriptions (404/410 responses) are cleaned up automatically. A startup CRITICAL log fires if VAPID keys are absent so production misconfiguration is immediately visible.
- **Milestone photo evidence**: homeowner defines ordered milestones on `awarded`/`in_progress` jobs; accepted contractor uploads photo evidence; milestone moves `pending → submitted`; homeowner approves or rejects. Optional AI analysis available via `?analyse=true`.
- **Reviews RLS hardening** (migration 016): any `USING (true)` SELECT policy is dropped; two narrowly-scoped policies replace it — reviewers can always read their own submission, reviewees can read reviews about them only after the double-blind lifts. Column-level `REVOKE SELECT (private_feedback)` is re-asserted as belt-and-braces.
- **Soft-delete**: `bids` and `reviews` both have `deleted_at TIMESTAMPTZ` and `deleted_by_user_id UUID` columns. `DELETE /jobs/{id}/bids/{bid_id}` (contractor only, pending bids only) and `DELETE /reviews/{id}` (reviewer only) stamp these columns rather than removing rows, preserving audit trail for dispute resolution.

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
# --execution-environment gen2  removes the 32 MiB request-body cap present in
#   gen1, allowing video uploads up to the app-level 350 MB limit.
# --memory 2Gi                  ensures the container can buffer a full 350 MB
#   upload alongside the Gemini SDK overhead without OOM-killing the instance.
gcloud run deploy stable-gig \
  --image gcr.io/gen-lang-client-0428658103/stable-gig \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated \
  --execution-environment gen2 \
  --memory 2Gi \
  --set-env-vars SUPABASE_URL=https://szpgcvfemllcsajryyuv.supabase.co \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest \
  --project=gen-lang-client-0428658103
```

### Subsequent deploys (after code changes)

**Step 1 — build and push the image:**
```bash
gcloud builds submit backend/ \
  --tag gcr.io/gen-lang-client-0428658103/stable-gig \
  --project=gen-lang-client-0428658103
```

**Step 2 — deploy the new image:**
```bash
gcloud run deploy stable-gig \
  --image gcr.io/gen-lang-client-0428658103/stable-gig \
  --platform managed \
  --region europe-west1 \
  --execution-environment gen2 \
  --memory 2Gi \
  --set-env-vars SUPABASE_URL=https://szpgcvfemllcsajryyuv.supabase.co \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest \
  --project=gen-lang-client-0428658103
```

### Deploying Edge Functions

```bash
supabase functions deploy analyse
supabase functions deploy analyse-video
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
