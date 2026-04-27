# CLAUDE-cars.md

## Project overview

**stable-gig (vehicle_damage vertical)** — a two-sided marketplace for vehicle repair. Vehicle owners upload photos or video of damage, get an AI assessment, post the job, and hire a garage or panel beater. Garages browse open jobs, submit quotes, and get paid via escrow on completion. Reviews are AI-summarised by Claude into a Pros/Cons list on the garage's profile.

This vertical runs on the **same codebase** as the home-repair marketplace. `VERTICAL=vehicle_damage` in the environment switches AI prompts, job categories, and UI copy without any code change.

There are **two separate frontends**:
- **FastAPI-served SPA** (`backend/static/index.html`) — default UI at the Cloud Run URL, served at `/`
- **Lovable PWA** — separate React app hosted in Lovable; installable on mobile (iOS/Android)

## Running locally

```bash
cd backend
pip install -r requirements.txt
# .env must contain GEMINI_API_KEY (and optionally SUPABASE_* keys) plus:
echo "VERTICAL=vehicle_damage" >> .env
uvicorn main:app --reload --port 8000
# App is at http://localhost:8000
```

## Running the tests

```bash
cd backend
pip install -r requirements.txt -r requirements-test.txt
pytest            # 447 tests, ~2 s, no API keys needed
pytest -v
```

The test suite runs with `VERTICAL=home_repair` (the default). All 110 tests covering the changed files pass with either vertical — the logic under test is vertical-agnostic.

## Key files — vehicle vertical

### Vertical config

| File | Purpose |
|------|---------|
| `backend/app/services/vertical_config.py` | Both vertical definitions (`_HOME_REPAIR`, `_VEHICLE_DAMAGE`) and `get_vertical_config()` accessor; **edit this file to change categories, prompts, or labels** |
| `backend/app/config.py` | `vertical: str = "home_repair"` pydantic setting; reads `VERTICAL` env var |

### FastAPI backend

| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI app — `GET /api/vertical` (frontend config), `GET /` (UI), `POST /analyse`, `POST /analyse/photos`, `/jobs`, `/jobs/{id}/bids` |
| `backend/app/routers/photo_analysis.py` | `POST /analyse/photos` — validates `trade_category` against `vertical_config["photo_categories"]` |
| `backend/app/services/photo_analyzer.py` | Image load, preprocess, sharpness check; Gemini 2.5 Flash with vertical-specific `system_intro` and `image_roles` |
| `backend/app/routers/jobs.py` | `POST/GET /jobs`, `GET/PATCH /jobs/{id}` — `_VALID_ACTIVITIES` resolved from `vertical_config["job_activities"]` at startup |
| `backend/app/services/task_breakdown.py` | Gemini task breakdown; uses `vertical_config["task_breakdown_role"]` and `["task_breakdown_provider"]` in prompt |
| `backend/app/routers/task_breakdown.py` | `POST /analyse/breakdown` endpoint |
| `backend/app/routers/bids.py` | `POST/GET /jobs/{id}/bids`, `PATCH`, `DELETE`, `GET /me/bids` — contractor bidding; soft-delete on pending bids |
| `backend/app/routers/reviews.py` | `POST /reviews`, `GET /reviews/contractor/{id}`, `GET /reviews/summary/{id}`, `DELETE /reviews/{id}` |
| `backend/app/routers/questions.py` | Anonymous garage Q&A per job; vehicle owner sees "Garage N" labels |
| `backend/app/routers/notifications.py` | VAPID public key; push subscription upsert/delete |
| `backend/app/routers/milestones.py` | Vehicle owner-defined milestones; garage photo evidence; approve/reject |
| `backend/app/services/push_service.py` | `notify_contractors_of_new_job()` — Web Push via pywebpush when a job is published |
| `backend/tests/conftest.py` | Shared fixtures + module stubs (pywebpush, google.generativeai, supabase) |

### Frontend — FastAPI-served SPA

| File | Purpose |
|------|---------|
| `backend/static/index.html` | **Deployed** SPA; calls `GET /api/vertical` on boot via `bootVertical()` to update title, subtitle, category dropdowns, and interests grid |
| `frontend/index.html` | Local dev copy — keep in sync with `backend/static/index.html` |

## Vehicle damage categories

Used in `trade_category` (photo analysis) and `activity` (jobs):

| Value | Label | Covers |
|-------|-------|--------|
| `bodywork` | Bodywork | Dents, scratches, panel replacement, paint damage |
| `mechanical` | Mechanical | Engine, gearbox, suspension, brakes, drivetrain |
| `electrical` | Electrical | Wiring, ECU, sensors, lights, infotainment |
| `tyres` | Tyres | Tyres, wheels, wheel alignment |
| `windscreen` | Windscreen | Windscreen, side/rear glass, mirrors |
| `interior` | Interior | Upholstery, dashboard, trim, seats |
| `general` | General | General vehicle issues that don't fit above |

## AI prompt design — vehicle_damage

The Gemini photo analysis prompt is defined in `vertical_config.py` under `_VEHICLE_DAMAGE`:

**System persona:**
> "You are an expert automotive damage assessor and panel beater with 30 years of hands-on experience diagnosing vehicle bodywork, mechanical, electrical, and interior damage for insurance claims and repair quotations."

**Image roles (positional):**

| Slot | Role | Instruction |
|------|------|-------------|
| 0 | Overview Shot | Vehicle make/model/colour; overall damage extent; which panels/areas affected |
| 1 | Close-up | Exact damage — dents, creases, scratches, paint damage, cracks, rust, deformation; estimate depth and area |
| 2 | Reference / Part ID | Part numbers, VIN plate, panel stampings, tyre sidewall markings, spec labels |
| 3 | Supplemental A | Resolve ambiguity; flag new damage or structural concerns |
| 4 | Supplemental B | Final view; confirm repair scope |

**Task breakdown persona:**
> "You are a professional automotive repair project planner. Break this repair into a clear, ordered sequence of practical tasks that a technician would follow on site."

## Architecture notes

- **VERTICAL env var** drives everything — categories, prompts, labels, and the `GET /api/vertical` response. Default is `home_repair`.
- **Single codebase, multiple verticals** — to add another vertical (e.g. `marine`, `hvac`), add a new entry to `_CONFIGS` in `vertical_config.py`. Nothing else needs to change.
- **`_VALID_ACTIVITIES`** in `jobs.py` is resolved from `get_vertical_config()["job_activities"]` once at module import time — it is a `frozenset`, so the startup vertical is fixed for the lifetime of the process.
- **Photo category validation** in `photo_analysis.py` calls `get_vertical_config()` at validation time (inside the Pydantic validator), so it also reads the startup config.
- **Frontend config** — `bootVertical()` in `index.html` fetches `GET /api/vertical` before auth boots, so category dropdowns and labels are correct before the user sees any UI.
- **Supabase schema is vertical-neutral** — `jobs.activity` is a plain `TEXT` column with no database-level constraint; validation is enforced in Python. This means you can run both verticals against the same Supabase project (jobs from each vertical coexist in the same table).
- **Tests** — the test suite uses `VERTICAL=home_repair` (default). If you want to test vehicle_damage-specific prompt content, set `VERTICAL=vehicle_damage` in the test environment or mock `get_vertical_config()` directly.

## `/api/vertical` endpoint

`GET /api/vertical` returns the full frontend config for the active vertical. The SPA calls this on boot:

```json
{
  "vertical":         "vehicle_damage",
  "app_title":        "Vehicle Damage Analyser",
  "owner_label":      "vehicle owner",
  "provider_label":   "garage",
  "providers_label":  "garages",
  "job_label":        "repair job",
  "categories": [
    { "value": "bodywork",   "label": "Bodywork",   "icon": "🚗" },
    { "value": "mechanical", "label": "Mechanical", "icon": "⚙️" },
    { "value": "electrical", "label": "Electrical", "icon": "⚡" },
    { "value": "tyres",      "label": "Tyres",      "icon": "🔄" },
    { "value": "windscreen", "label": "Windscreen", "icon": "🪟" },
    { "value": "interior",   "label": "Interior",   "icon": "🪑" },
    { "value": "general",    "label": "General",    "icon": "🛠️" }
  ],
  "job_activities":   ["bodywork", "electrical", "general", "interior", "mechanical", "tyres", "windscreen"],
  "photo_categories": ["bodywork", "electrical", "general", "interior", "mechanical", "tyres", "windscreen"]
}
```

## Supabase Edge Functions

Same functions as the home-repair vertical — all vertical-neutral:

| File | Purpose |
|------|---------|
| `supabase/functions/analyse/index.ts` | Gemini 2.0 Flash video analysis |
| `supabase/functions/contractors/index.ts` | Contractor/garage CRUD — register, fetch, update |
| `supabase/functions/review-sentiment/index.ts` | Claude Haiku: extract Pros/Cons from review; refresh `contractor_details.ai_review_summary` |

## Database migrations

The schema is shared with the home-repair vertical. See `README.md` for the full migration list. Key tables:

| Table | Relevance to vehicle vertical |
|-------|-------------------------------|
| `jobs` | `activity` stores vehicle categories (bodywork, mechanical, etc.) — no DB constraint, validated in Python |
| `contractors` | Represents garages / panel beaters; `expertise` array stores vehicle category values |
| `bids` | Garage bids on open jobs; soft-delete on pending bids |
| `reviews` | Escrow-gated reviews; Quality · Communication · Cleanliness sub-ratings |
| `job_milestones` / `milestone_photos` | Repair milestones with photo evidence |
| `push_subscriptions` | VAPID push subscriptions for garage notifications |

## Deploying to Cloud Run

Add `VERTICAL=vehicle_damage` to the deploy command — everything else is identical to the home-repair deploy:

```bash
gcloud run deploy stable-gig-cars \
  --image gcr.io/gen-lang-client-0428658103/stable-gig-cars \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated \
  --execution-environment gen2 \
  --memory 2Gi \
  --set-env-vars SUPABASE_URL=https://szpgcvfemllcsajryyuv.supabase.co,VERTICAL=vehicle_damage \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest \
  --project=gen-lang-client-0428658103
```

## Extending the vertical

To tune the vehicle_damage vertical, edit `_VEHICLE_DAMAGE` in `backend/app/services/vertical_config.py`:

- **Add or rename a category** — update `photo_categories`, `job_activities`, and `categories_display`
- **Change the AI persona or image roles** — update `system_intro` and `image_roles`
- **Change the task breakdown prompt** — update `task_breakdown_role` and `task_breakdown_provider`
- **Add a new UI label** — add a key to the dict and expose it in the `GET /api/vertical` response in `main.py`
