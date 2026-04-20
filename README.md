# stable-gig

A two-sided marketplace for home-repair tradesmen. Homeowners upload videos or photos of repair jobs, get an AI-powered assessment, post the job, and hire a vetted contractor. The platform handles the full lifecycle: job posting → bidding → escrow → completion → verified reviews.

**Live:** `https://stable-gig-374485351183.europe-west1.run.app`

---

## Architecture

Single-service backend on **Google Cloud Run** with two separate frontends:

- **FastAPI backend** — serves the REST API and a built-in SPA from one container; the primary web UI
- **Lovable PWA** — separate React app built in Lovable; installable on iOS / Android (see [PWA](#progressive-web-app-lovable) below)
- **Supabase** — Postgres database, Row Level Security, Auth (email / magic-link / Google OAuth), and Edge Functions (Deno)
- **Google Gemini** — video and photo AI assessment (2.5 Flash)
- **Anthropic Claude** — AI pros/cons extraction from review text (`review-sentiment` Edge Function)
- **Smarty** — US address autocomplete

---

## Features

| Feature | Detail |
|---------|--------|
| **Video analysis** | Upload a video → Gemini 2.5 Flash returns problem type, urgency, materials, clarifying questions, and extracted metadata (GPS, device, resolution) |
| **Photo analysis** | Upload 1–5 photos → Gemini 2.5 Flash runs Multi-Perspective Triangulation and returns a diagnosis, urgency score (1–10), required tools, and estimated parts |
| **RFP generation** | `POST /jobs/{id}/rfp` — Gemini assembles a professional RFP from analysis output + homeowner clarifications; includes scope of work, private cost estimate range (GBP pence), and permit flags |
| **Contractor matching** | `GET /jobs/{id}/contractors/matches` — ranks contractors by cosine similarity between their profile embedding and the job RFP; falls back to activity-category filter if no embeddings exist |
| **Profile embeddings** | `POST /me/contractor/embed-profile` — contractors generate a Gemini `text-embedding-004` profile vector (768-dim) so they surface in semantic matching |
| **Auth** | Email + password, magic-link OTP, and Google OAuth via Supabase |
| **Rate limiting** | 5/min on AI video analysis; 20/min on photo analysis; 5–10/min on auth endpoints (slowapi) |
| **Onboarding** | Two-step signup: profile (name, address) + trade interests |
| **Contractor registration** | Business name, postcode, phone, activity categories, licence/insurance details |
| **Job lifecycle** | `draft → open → awarded → in_progress → completed \| cancelled` |
| **Bidding** | Contractors bid on open jobs (`amount_pence` + scope note); homeowner accepts one bid — rejected bids are auto-closed; job moves to `awarded`; contractors can soft-delete pending bids |
| **Escrow gate** | `jobs.escrow_status` (`pending → held → funds_released`) unlocks the review flow |
| **Task breakdown** | `POST /analyse/breakdown` — Gemini decomposes a repair description into an ordered task list with titles, difficulty levels, and estimated durations |
| **Anonymous Q&A** | Contractors ask clarifying questions per job (`GET/POST /jobs/{id}/questions`); homeowner answers; contractor identity anonymised as "Contractor N" in homeowner view |
| **Web Push notifications** | When a job is published, contractors with matching activity categories receive a browser/PWA push notification (RFC 8030 + VAPID); requires `VAPID_*` env vars |
| **Milestone photo evidence** | Homeowner defines milestones on a job; contractor submits photo evidence per milestone; homeowner approves or rejects; optional AI analysis of submission |
| **Review system** | Transaction-anchored contractor reviews; `GET /reviews/summary/{id}` returns computed rating averages; soft-delete with audit trail |
| **Categorical ratings** | Quality · Communication · Cleanliness (1–5 each); overall rating auto-generated |
| **Private feedback** | Admin-only field on every review — never exposed to the tradesman |
| **AI review summary** | Claude Haiku extracts Pros/Cons from review text; aggregated profile-level summary stored on `contractor_details` |
| **Reviews RLS hardening** | Column-level REVOKE on `private_feedback`; narrowly-scoped SELECT policies (own submission + revealed about me); double-blind enforced at row level |
| **Progressive Web App** | Lovable frontend ships a web manifest + service worker; installable on iOS and Android home screen for a native-app feel |

---

## Project structure

```
backend/
├── main.py                              # FastAPI app — mounts all routers, serves frontend
├── requirements.txt                     # Runtime dependencies
├── requirements-test.txt                # Test-only dependencies
├── Dockerfile                           # Cloud Run container image
├── pytest.ini
├── app/
│   ├── config.py                        # Pydantic settings (reads env vars / .env)
│   ├── database.py                      # Supabase client singletons
│   ├── dependencies.py                  # get_current_user / get_optional_user
│   ├── models/schemas.py                # Shared Pydantic models
│   ├── routers/
│   │   ├── analyse.py                   # POST /analyse          (video, rate-limited)
│   │   ├── photo_analysis.py            # POST /analyse/photos   (photos, rate-limited)
│   │   ├── task_breakdown.py            # POST /analyse/breakdown
│   │   ├── jobs.py                      # POST/GET /jobs, GET/PATCH /jobs/{id}
│   │   ├── bids.py                      # POST/GET /jobs/{id}/bids, PATCH bid, DELETE bid, GET /me/bids
│   │   ├── reviews.py                   # POST /reviews, GET /reviews/contractor/{id}, summary, DELETE review
│   │   ├── questions.py                 # GET/POST /jobs/{id}/questions, PATCH answer
│   │   ├── notifications.py             # GET vapid-key, POST/DELETE /notifications/subscribe
│   │   ├── milestones.py                # POST/GET milestones, photos, PATCH approve/reject
│   │   ├── rfp.py                       # POST /jobs/{id}/rfp
│   │   ├── contractor_matching.py       # GET /jobs/{id}/contractors/matches
│   │   ├── auth.py                      # POST /auth/*
│   │   ├── profiles.py                  # GET/PATCH /me/profile
│   │   ├── user_metadata.py             # GET/PATCH /me/metadata
│   │   └── address.py                   # GET /address/zip, /address/autocomplete
│   └── services/
│       ├── gemini.py                    # Video → Gemini 2.0 Flash
│       ├── photo_analyzer.py            # Photos → Gemini 1.5 Flash
│       ├── task_breakdown.py            # Repair task list via Gemini
│       ├── push_service.py              # Web Push (pywebpush + VAPID)
│       ├── video_meta.py                # hachoir + mutagen metadata extraction
│       ├── smarty.py                    # Smarty address autocomplete / ZIP lookup
│       ├── contractor_matcher.py        # Cosine-similarity contractor ranking; expertise-filter fallback
│       ├── escrow.py                    # Escrow state machine (Stripe Payment Intents)
│       └── rfp_generator.py             # Gemini-powered RFP assembly from analysis output
├── static/
│   ├── index.html                       # Deployed frontend SPA
│   └── components/
│       ├── ReviewMediator.js            # Vanilla JS review flow component (deployed copy)
│       └── TradesmanRating.jsx          # React review form component (deployed copy)
├── supabase/
│   └── migrations/
│       ├── 001_initial_schema.sql       # profiles, auth hooks
│       ├── 002_user_metadata.sql        # user_metadata table
│       ├── 003_contractor_onboarding.sql # jobs, contractors, bids
│       ├── 004_clean_split.sql          # contractor_details, Clean Split redesign
│       ├── 005_rating_system.sql        # reviews, double-blind trigger, visible_reviews
│       ├── 006_categorical_ratings.sql  # escrow_status, sub-ratings, ai_pros_cons
│       ├── 007_quality_rating_private_feedback.sql  # quality sub-rating, private_feedback
│       ├── 008_private_feedback_column_security.sql # column-level REVOKE on private_feedback
│       ├── 009_jobs_analysis_result.sql # jobs.analysis_result JSONB
│       ├── 010_bidding_status_expansion.sql # full draft→open→awarded→in_progress→completed|cancelled
│       ├── 011_rfp_and_embeddings.sql   # rfp_content JSONB, profile_embedding vector(768)
│       ├── 012_escrow_transactions.sql  # escrow_transactions table
│       ├── 013_job_questions.sql        # job_questions table (anonymous contractor Q&A)
│       ├── 014_push_subscriptions.sql   # push_subscriptions table (Web Push VAPID)
│       ├── 015_job_milestones.sql       # job_milestones + milestone_photos tables
│       ├── 016_reviews_rls_hardening.sql # drop USING(true) policy; narrowly-scoped SELECT policies
│       └── 017_reconcile_missing_tables.sql # idempotent reconciliation: contractor_details, job_questions, push_subscriptions, job_milestones, milestone_photos (live-DB-compatible schemas)
└── tests/
    ├── conftest.py
    ├── test_photo_analyzer_service.py   # 32 unit tests
    ├── test_photo_analysis_router.py    # 30 integration tests
    ├── test_task_breakdown.py           # 18 tests
    ├── test_jobs_bids_router.py         # 30 tests
    ├── test_reviews_router.py           # 14 tests
    ├── test_questions_router.py         # 13 tests
    ├── test_notifications_router.py     # 8 tests
    ├── test_milestones_router.py        # 17 tests
    ├── test_push_service.py             # 9 tests
    ├── test_contractor_matcher_service.py  # 25 tests
    ├── test_escrow_service.py           # 35 tests
    └── test_rfp_generator_service.py    # 27 tests

frontend/
├── index.html                           # Local dev copy — keep in sync with backend/static/
└── components/
    ├── ReviewMediator.js                # Vanilla JS: escrow gate, categorical stars, AI reveal
    └── TradesmanRating.jsx              # React: 5-star form, private feedback, escrow logic

supabase/
└── functions/
    ├── analyse/index.ts                 # Edge Function: Gemini video analysis
    ├── contractors/index.ts             # Edge Function: contractor CRUD
    └── review-sentiment/index.ts        # Edge Function: Claude AI pros/cons extraction

scripts/
├── create_asana_tickets.py             # One-shot: file TradePhotoAnalyzer Asana tickets
├── seed_data.py                        # Populate test data (homeowners, contractors, jobs)
├── seed_helper.sql                     # RPC helpers for seed_data.py (apply once to Supabase)
├── generate_docs.ps1                   # Orchestrate openapi.json + feature-matrix.md + test-inventory.txt
├── generate_openapi.py                 # FastAPI → docs/generated/openapi.json
├── generate_feature_matrix.py          # Routes + test call sites → docs/generated/feature-matrix.md
└── docgen_utils.py                     # Shared loader / stubs for generation scripts

docs/
└── CustomerReviews.md                  # Full review system reference

.env.example                            # Template for all required env vars
```

---

## Running locally

```bash
cd backend
cp ../.env.example .env
# Fill in GEMINI_API_KEY and SUPABASE_* keys in .env

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# App at http://localhost:8000
```

### Environment variables

| Variable | Required | Where used |
|----------|----------|------------|
| `GEMINI_API_KEY` | Yes | Video + photo analysis (FastAPI + `analyse` Edge Function) |
| `ANTHROPIC_API_KEY` | For reviews | `review-sentiment` Edge Function (add in Supabase secrets) |
| `SUPABASE_URL` | For auth + DB | FastAPI + all Edge Functions |
| `SUPABASE_ANON_KEY` | For auth + DB | FastAPI + Edge Functions |
| `SUPABASE_SERVICE_KEY` | For admin ops | FastAPI (admin writes) + `review-sentiment` Edge Function (bypasses RLS) |
| `VAPID_PRIVATE_KEY` | For push | Web Push — base64url-encoded EC private key; omit to disable push |
| `VAPID_PUBLIC_KEY` | For push | Web Push — sent to browsers for subscription; exposed via `GET /notifications/vapid-public-key` |
| `VAPID_CLAIMS_EMAIL` | For push | Included in VAPID JWT, e.g. `mailto:admin@example.com` |
| `SMARTY_AUTH_ID` / `SMARTY_AUTH_TOKEN` | No | Address autocomplete (omit to disable) |

> **Edge Function secrets** are set separately in the Supabase Dashboard → Project Settings → Edge Functions → Secrets (not in `.env`).

---

## Tests

447 tests, ~2 s, no API keys or network access needed (all external services fully mocked).

```bash
cd backend
pip install -r requirements.txt -r requirements-test.txt
pytest       # all tests
pytest -v    # verbose
```

| File | Tests | Covers |
|------|-------|--------|
| `test_photo_analyzer_service.py`  | 32 | Sharpness detection · image loading · preprocessing (size guard, resize, blur flag, role assignment) · `analyse()` orchestrator |
| `test_photo_analysis_router.py`   | 30 | Request validation · error→HTTP status mapping · happy-path response shape |
| `test_task_breakdown.py`          | 18 | Router error mapping (429/502/422) · service validation · prompt content · float coercion · markdown fence stripping |
| `test_jobs_bids_router.py`        | 30 | Job CRUD · status transitions · contractor bid placement · accept/reject · auth guards |
| `test_reviews_router.py`          | 14 | Submit review · private_feedback stripped · duplicate detection · list + summary endpoints |
| `test_questions_router.py`        | 13 | Contractor Q&A · anonymisation · owner answers · auth guards |
| `test_notifications_router.py`    |  8 | VAPID key endpoint · subscribe/unsubscribe · VAPID-not-configured 503 |
| `test_milestones_router.py`       | 17 | Create milestones · list with photos · contractor photo submit · approve/reject |
| `test_push_service.py`            |  9 | VAPID config check · no-contractors skip · no-subscriptions skip · send + dead-subscription cleanup |
| `test_contractor_matcher_service.py` | 25 | Profile embedding · job query text · cosine similarity ranking · expertise-filter fallback |
| `test_escrow_service.py`          | 35 | Payment intent creation · held state · transfer · refund · status checks |
| `test_rfp_generator_service.py`   | 27 | RFP generation · prompt building · cost-estimate validation · Gemini call shape |

---

## Generated docs (code-first)

To generate user-facing documentation from the **actual code and tests** (instead of handwritten notes), run:

```bash
# from repo root
pwsh ./scripts/generate_docs.ps1 -SkipJunit
```

This writes canonical, code-derived docs into `docs/generated/`:

- `openapi.json` — produced from the FastAPI app object
- `feature-matrix.md` — endpoint-to-test mapping from router table + test call sites
- `test-inventory.txt` — discovered pytest test files and counts

Optional outputs:

- `api.html` (ReDoc) — generated when `npx` is installed and `-SkipHtml` is not set
- `tests-junit.xml` — generated unless `-SkipJunit` is set

CI enforces freshness via `.github/workflows/docs-from-code.yml` by regenerating docs and failing if `docs/generated/` differs from committed files.

---

## API reference

### `POST /analyse` — video

`multipart/form-data` with a `file` (video) field. Optional `browser_lat` / `browser_lon` fields supply GPS when the video has no embedded coordinates. Max upload: **30 MB** (Cloud Run's GFE load balancer enforces a 32 MB hard cap; the client-side check keeps uploads under this limit).

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

---

### `POST /analyse/photos` — photos

```json
{
  "images": ["data:image/jpeg;base64,…", "https://example.com/photo.jpg"],
  "description": "Damp patch on ceiling below the bathroom",
  "trade_category": "damp"
}
```

`images`: 1–5 entries (base64 data URI or HTTPS URL). Formats: JPEG, PNG, WebP.
`trade_category`: optional — one of `plumbing`, `electrical`, `structural`, `damp`, `roofing`, `general`.

Images are preprocessed before Gemini: resized to ≤1200 px, re-encoded as JPEG, sharpness-checked, and assigned positional roles (Wide Shot → Close-up → Scale/Context → Supplemental A/B).

---

### `POST /analyse/breakdown` — repair task list

Takes the output from either analysis endpoint and returns an ordered list of repair tasks. Pass the analysis response fields directly — no transformation required.

```json
// Request — fields map 1-to-1 from analysis responses
{
  "description":        "A dripping tap in the kitchen sink…",
  "problem_type":       "plumbing",
  "urgency":            "low",
  "materials_involved": ["copper pipe", "tap washer"],
  "required_tools":     ["adjustable spanner"]
}
```

```json
// Response
{
  "tasks": [
    { "title": "Shut off water supply under the sink", "difficulty_level": "easy",   "estimated_minutes": 5  },
    { "title": "Remove tap handle and packing nut",    "difficulty_level": "medium", "estimated_minutes": 15 },
    { "title": "Replace worn tap washer",              "difficulty_level": "easy",   "estimated_minutes": 10 },
    { "title": "Reassemble and test for leaks",        "difficulty_level": "easy",   "estimated_minutes": 10 }
  ]
}
```

`difficulty_level`: `easy` (any DIYer) · `medium` (trade experience needed) · `hard` (specialist / certification required)
`estimated_minutes`: on-site time only, excludes travel and parts sourcing

Uses the same `GEMINI_API_KEY` already required by the video and photo analysers — no additional credentials needed.

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

### Bids (`/jobs/{id}/bids`)

| Method   | Path | Auth | Description |
|----------|------|------|-------------|
| `POST`   | `/jobs/{job_id}/bids` | Contractor JWT | Submit a bid (`amount_pence` + `note`) on an `open` job |
| `GET`    | `/jobs/{job_id}/bids` | JWT | Job owner sees all bids; contractor sees only their own |
| `PATCH`  | `/jobs/{job_id}/bids/{bid_id}` | Homeowner JWT | Accept or reject a pending bid |
| `DELETE` | `/jobs/{job_id}/bids/{bid_id}` | Contractor JWT | Soft-delete a pending bid (sets `deleted_at`; only pending bids allowed) |
| `GET`    | `/me/bids` | Contractor JWT | All bids placed by the current contractor, newest first |

### Reviews (`/reviews`)

> **Live schema note:** the `reviews` table uses `contractor_id`, `comment`, and `overall` (generated column) — not the `reviewee_id`/`body`/`rating` names described in the legacy design docs.

| Method   | Path | Auth | Description |
|----------|------|------|-------------|
| `POST`   | `/reviews` | JWT | Submit a review for a contractor; `private_feedback` written to DB but never returned |
| `GET`    | `/reviews/contractor/{id}` | JWT | All reviews for a contractor, newest first |
| `GET`    | `/reviews/summary/{id}` | Public | Aggregate averages (`avg_rating`, `avg_cleanliness`, `avg_communication`, `avg_quality`, `review_count`) |
| `DELETE` | `/reviews/{review_id}` | JWT (reviewer only) | Soft-delete own review (sets `deleted_at`) |

### Feature flags

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET`  | `/config/feature-flags` | Public | Returns `push_notifications_enabled`, `stripe_enabled` — lets frontends gracefully degrade |

---

## Supabase Edge Functions

Deployed to the Supabase project (`szpgcvfemllcsajryyuv`). Each function lives in `supabase/functions/<name>/index.ts`.

| Function | Trigger | What it does |
|----------|---------|--------------|
| `analyse` | `POST /functions/v1/analyse` | Uploads video to Gemini Files API, polls until active, returns structured JSON assessment |
| `contractors` | `POST/GET/PATCH /functions/v1/contractors` | Full CRUD for contractor registration and `contractor_details` |
| `review-sentiment` | `POST /functions/v1/review-sentiment` | Reads a submitted review, calls **Claude Haiku** to extract Pros/Cons, writes `ai_pros_cons` back to the review, refreshes `contractor_details.ai_review_summary` |

### Deploying Edge Functions

```bash
supabase functions deploy analyse
supabase functions deploy contractors
supabase functions deploy review-sentiment
```

Required secrets (Supabase Dashboard → Project Settings → Edge Functions → Secrets):

```
GEMINI_API_KEY=…
ANTHROPIC_API_KEY=…       ← needed for review-sentiment
SUPABASE_URL=…
SUPABASE_ANON_KEY=…
SUPABASE_SERVICE_KEY=…
```

---

## Database migrations

Run in order against your Supabase project:

```bash
supabase db push   # applies all pending migrations
# or apply manually:
psql $DATABASE_URL -f backend/supabase/migrations/001_initial_schema.sql
# … repeat for 002–007
```

| Migration | Key additions |
|-----------|---------------|
| `001_initial_schema` | `profiles`, auth trigger |
| `002_user_metadata` | `user_metadata` |
| `003_contractor_onboarding` | `jobs`, `contractors`, `bids` |
| `004_clean_split` | `contractor_details`, Clean Split identity design (`contractors.id = auth.users.id`) |
| `005_rating_system` | `reviews`, double-blind trigger, `visible_reviews` view, `contractor_rating()` / `client_rating()` helpers |
| `006_categorical_ratings` | `jobs.escrow_status`; sub-ratings (Cleanliness · Communication · Accuracy); `reviews.ai_pros_cons`; `contractor_details.ai_review_summary` |
| `007_quality_rating_private_feedback` | Renames `rating_accuracy → rating_quality`; adds `reviews.private_feedback` (admin-only, excluded from `visible_reviews`) |
| `008_private_feedback_column_security` | Column-level `REVOKE SELECT (private_feedback)` from `authenticated`/`anon`; explicit column grants for all other fields |
| `009_jobs_analysis_result` | `jobs.analysis_result JSONB` — stores Gemini analysis output with the job |
| `010_bidding_status_expansion` | Expands `jobs.status` to full lifecycle: `draft \| open \| awarded \| in_progress \| completed \| cancelled` |
| `011_rfp_and_embeddings` | `jobs.rfp_content JSONB`; `contractors.profile_embedding vector(768)` for semantic matching |
| `012_escrow_transactions` | `escrow_transactions` audit table |
| `013_job_questions` | `job_questions` — anonymous contractor Q&A per job; RLS: owner sees all, contractor sees own |
| `014_push_subscriptions` | `push_subscriptions` — Web Push endpoint/key storage per user; UNIQUE on `(user_id, endpoint)` |
| `015_job_milestones` | `job_milestones` + `milestone_photos` — homeowner-defined milestones with contractor photo evidence |
| `016_reviews_rls_hardening` | Drops any `USING (true)` SELECT policy; adds narrowly-scoped policies (own submission + revealed about me); re-asserts column-level REVOKE |
| `017_reconcile_missing_tables` | Idempotent reconciliation (`IF NOT EXISTS`): creates `contractor_details` (1-to-1 with `contractors.id`, with auto-trigger), `job_questions`, `push_subscriptions`, `job_milestones`, `milestone_photos` using live-DB-compatible schemas (`user_id` FK on `contractors`, `expertise` array); backfills `contractor_details` for existing rows |

Apply: `supabase db push` or run each file manually with `psql`.

Full review system documentation: [`docs/CustomerReviews.md`](docs/CustomerReviews.md)

---

## Review & rating system

The platform uses a **double-blind, escrow-gated review system**:

- Reviews are locked until `jobs.escrow_status = 'funds_released'`
- Both client and contractor rate each other after job completion
- Ratings are hidden until both submit (or 14 days pass)
- Three sub-dimensions — **Quality**, **Communication**, **Cleanliness** — replace a single star; overall rating is a generated average
- A `private_feedback` field on each review is visible only to platform admins (excluded from `visible_reviews`)
- Claude Haiku summarises review text into a Pros/Cons list, displayed on the contractor's profile

### Frontend components

Two components are provided — use whichever fits your frontend stack:

| Component | Stack | File |
|-----------|-------|------|
| `ReviewMediator` | Vanilla JS (no dependencies) | `frontend/components/ReviewMediator.js` |
| `TradesmanRating` | React + `@supabase/supabase-js` | `frontend/components/TradesmanRating.jsx` |

Both are kept in sync between `frontend/components/` (source) and `backend/static/components/` (deployed copy served by FastAPI).

---

## Deploying to Cloud Run

**Project:** `gen-lang-client-0428658103` · **Region:** `europe-west1` · **Service:** `stable-gig`

Secrets (`GEMINI_API_KEY`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`) are stored in GCP Secret Manager and mounted at runtime.

> Run as **two separate commands** — the build must complete before the deploy can reference the new image.

```bash
# Step 1 — build and push the container image
gcloud builds submit backend/ \
  --tag gcr.io/gen-lang-client-0428658103/stable-gig \
  --project=gen-lang-client-0428658103
```

```bash
# Step 2 — deploy
# --execution-environment gen2  required: removes the 32 MB gen1 request-body cap
# --memory 2Gi                  buffers video uploads + Gemini SDK overhead
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

### First-time secret setup

```bash
echo -n "KEY" | gcloud secrets create GEMINI_API_KEY     --data-file=- --project=gen-lang-client-0428658103
echo -n "KEY" | gcloud secrets create SUPABASE_ANON_KEY  --data-file=- --project=gen-lang-client-0428658103
echo -n "KEY" | gcloud secrets create SUPABASE_SERVICE_KEY --data-file=- --project=gen-lang-client-0428658103
```

To rotate: `echo -n "NEW_KEY" | gcloud secrets versions add SECRET_NAME --data-file=- --project=gen-lang-client-0428658103`, then redeploy.

---

## Frontend notes

### FastAPI-served SPA

- `backend/static/index.html` and `frontend/index.html` are identical. Edit one, copy to the other. Same rule applies to files in `frontend/components/` ↔ `backend/static/components/`.
- The SPA handles `/`, `/login`, `/signup`, and `/dashboard` — all served by FastAPI.
- Auth tokens are stored in `sessionStorage` (cleared on tab close).
- The dashboard has two tabs: **Video Analysis** and **Photo Analysis**.

### Progressive Web App (Lovable)

The production mobile experience is a **PWA built in Lovable** — a separate React project that is not stored in this repository.

| Property | Detail |
|----------|--------|
| Hosted by | Lovable (own domain / Vercel-backed CDN) |
| Installable | Yes — iOS Safari "Add to Home Screen" and Android Chrome install prompt |
| Offline support | Service worker caches the app shell; API calls require connectivity |
| Backend | Same Cloud Run URL; all requests are cross-origin |
| CORS | Backend uses `allow_origins=["*"]` specifically to support this |
| Upload limit | 30 MB client-side (enforced in the Lovable app); matches the Cloud Run GFE wall |
| Auth | Supabase JWT sent as `Authorization: Bearer <token>`; `POST /analyse/photos` requires auth; `POST /analyse` (video) is public |

To make changes to the Lovable PWA, edit it in the Lovable editor. Changes to the backend API (new fields, new endpoints) should be reflected in the Lovable project and documented here.
