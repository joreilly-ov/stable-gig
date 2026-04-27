# stable-gig — Vehicle Damage Vertical

A two-sided marketplace for vehicle repair. Vehicle owners upload photos or video of damage, get an AI-powered assessment, post the job, and hire a vetted garage or panel beater. The platform handles the full lifecycle: damage assessment → job posting → bidding → escrow → completion → verified reviews.

This is the **vehicle_damage** vertical of the stable-gig codebase. The same backend, database schema, auth, bidding, escrow, and review infrastructure is shared with the home-repair vertical — only the AI prompts, job categories, and UI copy differ.

---

## What's different from the home-repair vertical

| Aspect | home_repair | vehicle_damage |
|--------|-------------|----------------|
| **Owner label** | homeowner | vehicle owner |
| **Provider label** | contractor | garage |
| **AI persona** | Multi-trade diagnostic engineer | Automotive damage assessor / panel beater |
| **Image roles** | Wide Shot · Close-up · Scale/Context | Overview Shot · Close-up · Reference/Part ID |
| **Job categories** | plumbing, electrical, structural, damp, roofing, general | bodywork, mechanical, electrical, tyres, windscreen, interior, general |
| **Task breakdown** | Home repair project planner / tradesperson | Automotive repair project planner / technician |
| **App title** | Home Repair Analyser | Vehicle Damage Analyser |

Everything is switched by setting `VERTICAL=vehicle_damage` in the environment.

---

## Architecture

Single-service backend on **Google Cloud Run** with two separate frontends:

- **FastAPI backend** — serves the REST API and a built-in SPA from one container
- **Lovable PWA** — separate React app (installable on iOS / Android)
- **Supabase** — Postgres database, RLS, Auth, and Edge Functions
- **Google Gemini** — photo and video damage assessment (2.5 Flash)
- **Anthropic Claude** — AI pros/cons extraction from review text (`review-sentiment` Edge Function)

---

## Running locally

```bash
cd backend
cp ../.env.example .env
# Add your GEMINI_API_KEY and SUPABASE_* keys, then set the vertical:
echo "VERTICAL=vehicle_damage" >> .env

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# App at http://localhost:8000
```

On startup the app title changes to **Vehicle Damage Analyser**, the category dropdown shows vehicle categories, and the Gemini prompts are tuned for automotive damage assessment.

---

## Environment variables

| Variable | Required | Notes |
|----------|----------|-------|
| `VERTICAL` | No | `vehicle_damage` — defaults to `home_repair` if omitted |
| `GEMINI_API_KEY` | Yes | Photo + video analysis |
| `ANTHROPIC_API_KEY` | For reviews | `review-sentiment` Edge Function (Supabase secrets) |
| `SUPABASE_URL` | For auth + DB | FastAPI + Edge Functions |
| `SUPABASE_ANON_KEY` | For auth + DB | FastAPI + Edge Functions |
| `SUPABASE_SERVICE_KEY` | For admin ops | FastAPI (admin writes, bypasses RLS) |
| `VAPID_PRIVATE_KEY` | For push | Web Push; omit to disable |
| `VAPID_PUBLIC_KEY` | For push | Sent to browsers for subscription |
| `VAPID_CLAIMS_EMAIL` | For push | e.g. `mailto:admin@example.com` |
| `STRIPE_SECRET_KEY` | For payments | Escrow; omit to disable |

---

## Features

| Feature | Detail |
|---------|--------|
| **Photo analysis** | Upload 1–5 photos of vehicle damage → Gemini 2.5 Flash assigns each image a role (Overview, Close-up, Reference/Part ID) and returns a damage assessment with urgency score, required tools, and estimated parts |
| **Video analysis** | Upload a video → Gemini 2.5 Flash returns damage type, urgency, materials, and clarifying questions |
| **Task breakdown** | `POST /analyse/breakdown` — Gemini decomposes the damage description into an ordered repair task list (titles, difficulty, estimated minutes) |
| **Job lifecycle** | `draft → open → awarded → in_progress → completed \| cancelled` |
| **Bidding** | Garages bid on open jobs (`amount_pence` + scope note); vehicle owner accepts one bid; rejected bids auto-close |
| **Escrow** | Funds held until job completion; unlocks the review flow |
| **RFP generation** | `POST /jobs/{id}/rfp` — Gemini assembles a professional repair brief from analysis output |
| **Contractor matching** | `GET /jobs/{id}/contractors/matches` — ranks garages by semantic similarity to the job RFP |
| **Anonymous Q&A** | Garages ask clarifying questions; vehicle owner answers; garage identity anonymised |
| **Milestone photo evidence** | Vehicle owner defines milestones; garage submits photo evidence; owner approves/rejects |
| **Web Push** | When a job is published, garages with matching expertise receive a push notification |
| **Review system** | Escrow-gated, double-blind reviews; Quality · Communication · Cleanliness sub-ratings; AI Pros/Cons via Claude Haiku |
| **Progressive Web App** | Lovable-built PWA; installable on iOS/Android |

---

## Job categories

The `activity` field on jobs and the `trade_category` field on photo analysis both accept:

| Value | Label | Description |
|-------|-------|-------------|
| `bodywork` | Bodywork | Dents, scratches, panel replacement, paint |
| `mechanical` | Mechanical | Engine, gearbox, suspension, brakes |
| `electrical` | Electrical | Wiring, ECU, sensors, lights |
| `tyres` | Tyres | Tyres, wheels, alignment |
| `windscreen` | Windscreen | Windscreen, windows, mirrors |
| `interior` | Interior | Upholstery, dashboard, trim |
| `general` | General | General vehicle issues |

---

## API reference

### `POST /analyse/photos` — damage assessment from photos

```json
{
  "images": ["data:image/jpeg;base64,…", "https://example.com/damage.jpg"],
  "description": "Front-left corner impact — dented bumper and crumpled wing panel",
  "trade_category": "bodywork"
}
```

`images`: 1–5 entries (base64 data URI or HTTPS URL). JPEG, PNG, WebP.
`trade_category`: optional — one of the vehicle categories above.

Images are preprocessed and assigned roles:

| Slot | Role | What Gemini looks for |
|------|------|-----------------------|
| 1 | Overview Shot | Vehicle make/model, overall damage extent, which panels are affected |
| 2 | Close-up | Exact damage detail — dents, creases, cracks, paint loss, depth estimate |
| 3 | Reference / Part ID | Part numbers, VIN plate, panel stampings, tyre markings |
| 4 | Supplemental A | Additional damage angle, any new structural concerns |
| 5 | Supplemental B | Final view — confirms repair scope |

Response:

```json
{
  "likely_issue": "Front-left corner impact: crumpled wing panel and bumper requiring replacement",
  "urgency_score": 4,
  "required_tools": ["panel puller", "MIG welder", "dent bar set"],
  "estimated_parts": ["front wing panel (left)", "front bumper cover", "bumper reinforcement bar"],
  "image_feedback": [
    { "index": 0, "role": "Overview Shot", "quality": "ok", "note": null }
  ],
  "token_usage_estimate": { "prompt_tokens": 1240, "completion_tokens": 180, "total_tokens": 1420 }
}
```

`urgency_score`: 1 = cosmetic (can wait), 10 = immediate safety risk (e.g. structural failure, brake damage).

---

### `POST /analyse/breakdown` — repair task list

```json
{
  "description": "Front-left wing panel crumpled, bumper broken at mounting points",
  "problem_type": "bodywork",
  "urgency": "medium",
  "materials_involved": ["front wing panel (left)", "front bumper cover"],
  "required_tools": ["panel puller", "MIG welder"]
}
```

```json
{
  "tasks": [
    { "title": "Assess structural damage to chassis rail",    "difficulty_level": "medium", "estimated_minutes": 20 },
    { "title": "Remove damaged wing panel and bumper",        "difficulty_level": "easy",   "estimated_minutes": 30 },
    { "title": "Source replacement panels and check fit",     "difficulty_level": "easy",   "estimated_minutes": 15 },
    { "title": "Weld and align new wing panel",               "difficulty_level": "hard",   "estimated_minutes": 90 },
    { "title": "Fit bumper cover and check alignment",        "difficulty_level": "medium", "estimated_minutes": 30 },
    { "title": "Prime, paint, and lacquer repaired panels",   "difficulty_level": "hard",   "estimated_minutes": 120 },
    { "title": "Final inspection and road test",              "difficulty_level": "easy",   "estimated_minutes": 20 }
  ]
}
```

---

### `GET /api/vertical` — vertical config (frontend use)

Returns the active vertical configuration. The frontend calls this on boot to populate category dropdowns and update domain labels.

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

---

## Tests

```bash
cd backend
pip install -r requirements.txt -r requirements-test.txt
pytest
```

110 tests directly cover the photo analysis, task breakdown, and job/bid flows — all pass with `VERTICAL=vehicle_damage` (the default test environment uses `home_repair`, but the logic under test is vertical-agnostic).

---

## Deploying to Cloud Run

Add `VERTICAL=vehicle_damage` as an environment variable in the deploy command:

```bash
# Step 1 — build
gcloud builds submit backend/ \
  --tag gcr.io/gen-lang-client-0428658103/stable-gig-cars \
  --project=gen-lang-client-0428658103

# Step 2 — deploy
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

---

## Where the vertical logic lives

| File | Role |
|------|------|
| `backend/app/services/vertical_config.py` | Both vertical definitions; `get_vertical_config()` accessor |
| `backend/app/config.py` | `vertical: str = "home_repair"` setting (reads `VERTICAL` env var) |
| `backend/app/services/photo_analyzer.py` | Calls `get_vertical_config()["system_intro"]` and `["image_roles"]` |
| `backend/app/routers/photo_analysis.py` | Validates `trade_category` against `get_vertical_config()["photo_categories"]` |
| `backend/app/routers/jobs.py` | Resolves `_VALID_ACTIVITIES` from `get_vertical_config()["job_activities"]` at startup |
| `backend/app/services/task_breakdown.py` | Uses `["task_breakdown_role"]` and `["task_breakdown_provider"]` in the Gemini prompt |
| `backend/main.py` | `GET /api/vertical` endpoint; app title driven by vertical |
| `backend/static/index.html` | `bootVertical()` fetches `/api/vertical` on load and updates title, subtitle, dropdowns |

To add a new vertical, add an entry to `_CONFIGS` in `vertical_config.py` — nothing else needs to change.
