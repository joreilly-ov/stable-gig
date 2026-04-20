# Marketplace Rating & Review System

## Overview

The review system is modelled on platforms like Uber and Upwork: **both parties rate each other after a job completes**, and reviews are **tied to a real transaction** (the `job_id`) so fake reviews are structurally impossible.

It is a **double-blind** system — neither party can see what the other wrote until both have submitted, or a 14-day fallback timer expires. This prevents scores being influenced by the other person's review.

Reviews capture **three categorical dimensions** (Quality, Communication, Cleanliness) rather than a single star score. An overall rating is automatically generated as their average. A **Claude-powered Edge Function** extracts a Pros/Cons summary from the free-text body and displays it on the contractor's profile.

---

## Live implementation vs. designed system

> **Important:** the live database was set up manually before the migration files were applied.
> The `reviews.py` API layer reflects the **actual live schema**, which is a simplified version
> of the full double-blind design described later in this document.
>
> | Aspect | Live schema (what code uses) | Designed schema (migrations 005–008) |
> |--------|------------------------------|---------------------------------------|
> | Reviewer identity | `reviewer_id` | `reviewer_id` + `reviewer_role` |
> | Reviewee identity | `contractor_id` | `reviewee_id` + `reviewee_role` |
> | Free text | `comment` | `body` |
> | Overall score | `overall` (generated) | `rating` (generated) |
> | Double-blind | Not in live API layer | `content_visible`, `reveal_at`, trigger |
> | AI enrichment | `ai_pros_cons` (via Edge Function) | Same |
> | Soft-delete | `deleted_at`, `deleted_by_user_id` | Not in original design |
>
> The rest of this document describes both layers; sections marked **[Live]** reflect the
> deployed API; sections marked **[Designed]** describe the full aspirational system.

## Core design principles

| Principle | How it is enforced |
|---|---|
| **Transaction-anchored** | Every review references a `job_id`. No job = no review. |
| **Escrow-gated** | `ReviewMediator` component only renders when `jobs.escrow_status = 'funds_released'`. |
| **One review per reviewer per job** | `UNIQUE (job_id, reviewer_id)` database constraint. |
| **Double-blind** [Designed] | `content_visible = FALSE` by default; trigger reveals both reviews simultaneously when the second is submitted. Not active in the current live API layer. |
| **Soft-delete audit trail** [Live] | `DELETE /reviews/{id}` stamps `deleted_at` + `deleted_by_user_id` instead of removing the row; deleted rows remain for dispute resolution. |
| **AI-enriched** | Claude (Haiku) extracts Pros/Cons from the free-text via the `review-sentiment` Edge Function. |

---

## Migration files

| File | What it adds |
|---|---|
| `backend/supabase/migrations/005_rating_system.sql` | `reviews` table, double-blind trigger, `visible_reviews` view, rating helpers |
| `backend/supabase/migrations/006_categorical_ratings.sql` | `escrow_status` on jobs; replaces single `rating` with three sub-ratings (Cleanliness/Communication/Accuracy); adds `ai_pros_cons`; adds `ai_review_summary` to `contractor_details` |
| `backend/supabase/migrations/007_quality_rating_private_feedback.sql` | Renames `rating_accuracy → rating_quality`; adds `private_feedback TEXT` (admin-only, excluded from `visible_reviews`); recreates view and helpers |
| `backend/supabase/migrations/008_private_feedback_column_security.sql` | Column-level `REVOKE SELECT (private_feedback)` from `authenticated`/`anon`; explicit `GRANT SELECT` for all other columns |
| `backend/supabase/migrations/016_reviews_rls_hardening.sql` | Drops any broad `USING (true)` SELECT policy; adds `"reviews: select own submission"` and `"reviews: select revealed about me"` policies; re-asserts column REVOKE as belt-and-braces |

---

## Job status & escrow lifecycle

Two independent state machines run on the `jobs` table:

### `status` — job progress
```
draft → open → awarded → in_progress → completed | cancelled
```

| Status | Meaning |
|---|---|
| `draft` | Job created, not yet visible to contractors |
| `open` | Job posted, accepting bids |
| `awarded` | A bid has been accepted |
| `in_progress` | Work has started |
| `completed` | Work done and both reviews submitted (trigger advances this) |
| `cancelled` | Job cancelled at any stage |

> **Note:** status transitions are controlled by the homeowner via `PATCH /jobs/{id}`. Allowed transitions: `draft → open \| cancelled`, `open → cancelled`, `awarded → in_progress \| cancelled`, `in_progress → completed \| cancelled`.

### `escrow_status` — payment state
```
pending → held → funds_released | refunded
```

| Status | Meaning |
|---|---|
| `pending` | No payment yet |
| `held` | Funds are in escrow |
| `funds_released` | Payment released to contractor — **reviews unlock here** |
| `refunded` | Funds returned to client |

> **Payment integration note:** When the payment layer releases escrow, set **both**:
> ```sql
> UPDATE jobs
>    SET status        = 'awaiting_review',
>        escrow_status = 'funds_released'
>  WHERE id = $job_id;
> ```
> `ReviewMediator` checks `escrow_status` before rendering; the double-blind trigger checks `status` when advancing to `completed`.

---

## The `reviews` table

### Live schema [Live]

```sql
reviews (
    id                   UUID        PRIMARY KEY
    job_id               TEXT        → jobs.id (stored as TEXT in live DB)
    contractor_id        UUID        → contractors.id   -- who is being reviewed
    reviewer_id          UUID        → auth.users.id    -- who wrote this review

    -- Categorical sub-ratings (all required, 1–5)
    rating_quality       SMALLINT    1–5
    rating_communication SMALLINT    1–5
    rating_cleanliness   SMALLINT    1–5

    -- Generated overall (read-only)
    overall              NUMERIC(3,2) GENERATED ALWAYS AS avg(sub-ratings)

    comment              TEXT        free-text review body
    ai_pros_cons         JSONB       { pros, cons, one_line_summary } — filled async
    private_feedback     TEXT        admin-only — excluded from API responses
    created_at           TIMESTAMPTZ

    -- Soft-delete audit trail
    deleted_at           TIMESTAMPTZ NULL
    deleted_by_user_id   UUID        NULL
)
```

### Designed schema (migrations 005–008) [Designed]

```sql
reviews (
    id                   UUID        PRIMARY KEY
    job_id               UUID        → jobs.id           -- escrow/transaction anchor
    reviewer_id          UUID        → auth.users.id     -- who wrote this review
    reviewee_id          UUID        → auth.users.id     -- who is being reviewed
    reviewer_role        TEXT        'client' | 'contractor'
    reviewee_role        TEXT        'client' | 'contractor'

    -- Categorical sub-ratings (all required, 1–5)
    rating_quality       SMALLINT    1–5
    rating_communication SMALLINT    1–5
    rating_cleanliness   SMALLINT    1–5

    -- Generated overall rating (read-only)
    rating               NUMERIC(3,2) GENERATED ALWAYS AS avg(sub-ratings)

    body                 TEXT        free-text (hidden until revealed)
    ai_pros_cons         JSONB       { pros, cons, one_line_summary } — filled async
    private_feedback     TEXT        admin-only — never in visible_reviews
    content_visible      BOOLEAN     FALSE until peer reviews or timer expires
    reveal_at            TIMESTAMPTZ submitted_at + 14 days (fallback)
    submitted_at         TIMESTAMPTZ
)
```

### Sub-rating meanings

| Dimension | Client → Contractor | Contractor → Client |
|---|---|---|
| **Quality** | How good was the overall standard of work? | How clearly was the job scoped / briefed? |
| **Communication** | Did they communicate well throughout? | Did the client communicate clearly? |
| **Cleanliness** | How clean was the work area afterwards? | How clean / accessible was the property? |

### Identity mapping

Because the codebase uses the **Clean Split** design (`contractors.id = profiles.id = auth.users.id`), both the client and contractor are identified by their `auth.users` UUID.

- **Client** = `jobs.user_id`
- **Contractor** = `contractors.id` (= their `auth.users` UUID)

---

## Double-blind mechanism

### How it works

```
Client submits review           Contractor submits review
        │                                │
        ▼                                ▼
content_visible = FALSE          content_visible = FALSE
reveal_at = now + 14 days        reveal_at = now + 14 days
        │                                │
        └──────── trigger fires ─────────┘
                       │
              peer review found?
                 YES ──────► flip BOTH to content_visible = TRUE
                             advance job to 'completed'
                 NO  ──────► leave FALSE; reveal_at handles it
```

### The 14-day fallback

If one party never submits a review, the other party's review body automatically becomes readable after 14 days. This is handled **at query time** in the `visible_reviews` view — no cron job or background worker is required:

```sql
CASE
    WHEN content_visible OR reveal_at <= NOW() THEN body
    ELSE NULL
END AS body
```

### Always use `visible_reviews`, not `reviews`

The raw `reviews` table contains hidden body text and AI summaries. **All application queries should use the `visible_reviews` view**, which enforces the double-blind automatically. `ai_pros_cons` is also hidden until revealed.

```sql
-- Correct
SELECT * FROM visible_reviews WHERE reviewee_id = $1;

-- Wrong — exposes hidden content
SELECT * FROM reviews WHERE reviewee_id = $1;
```

---

## AI Sentiment — `review-sentiment` Edge Function

### File
`supabase/functions/review-sentiment/index.ts`

### What it does

Called by `ReviewMediator` immediately after a review is inserted (fire-and-forget, non-blocking). It:

1. Fetches the review from the DB (via service role, bypasses RLS)
2. Calls **Claude Haiku** (`claude-haiku-4-5-20251001`) with a structured prompt
3. Extracts a Pros/Cons list + one-line summary from the free-text body
4. Writes the result to `reviews.ai_pros_cons`
5. If the reviewer was a client, refreshes `contractor_details.ai_review_summary` — an aggregated top-3 pros/cons built from all the contractor's revealed reviews

### Request

```
POST /functions/v1/review-sentiment
Authorization: Bearer <user-jwt>
Content-Type: application/json

{ "review_id": "<uuid>" }
```

### Response

```json
{
  "success": true,
  "ai_pros_cons": {
    "pros": ["Arrived on time", "Tidy finish"],
    "cons": ["Quote slightly underestimated materials"],
    "one_line_summary": "Reliable tradesperson with good communication but minor cost overrun."
  }
}
```

### Required secret

Add in **Supabase Dashboard → Project Settings → Edge Functions → Secrets**:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### Contractor profile summary shape

`contractor_details.ai_review_summary` stores an aggregated view built from all revealed reviews:

```json
{
  "top_pros":     ["Consistently punctual", "Clean work area"],
  "top_cons":     ["Occasional cost overruns"],
  "last_updated": "2026-03-18T10:00:00.000Z",
  "review_count": 12
}
```

---

## ReviewMediator component

### Files

| File | Purpose |
|---|---|
| `frontend/components/ReviewMediator.js` | Source (keep in sync) |
| `backend/static/components/ReviewMediator.js` | Deployed copy served by FastAPI |

### What it does

A self-contained vanilla-JS class that handles the complete review flow:

1. **Escrow gate** — fetches the job record; renders a locked placeholder unless `escrow_status === 'funds_released'`
2. **Already reviewed?** — checks for an existing submission; skips the form if found
3. **Form** — three star-rating rows (Cleanliness / Communication / Accuracy) plus an optional free-text body with a 2000-character counter
4. **Submit** — POSTs to `reviews` via PostgREST, then calls `review-sentiment` in the background
5. **AI summary** — displays the Pros/Cons once the Edge Function responds
6. **Peer-reveal polling** — polls `visible_reviews` every 15 s; when `is_revealed` flips to `true`, shows the other party's review and their AI summary

### Mounting

```html
<div id="review-mount"></div>
<script src="/components/ReviewMediator.js"></script>
<script>
  const rm = new ReviewMediator({
    container:        document.getElementById("review-mount"),
    supabaseUrl:      "https://xxxx.supabase.co",
    accessToken:      session.access_token,    // JWT from Supabase auth
    edgeFunctionBase: "https://xxxx.supabase.co/functions/v1",
    jobId:            "<job-uuid>",
    reviewerId:       "<current-user-uuid>",
    revieweeId:       "<other-party-uuid>",
    reviewerRole:     "client",                // "client" | "contractor"
    revieweeRole:     "contractor",
    revieweeName:     "Dave's Plumbing",       // shown in the UI
  });
  rm.mount();
</script>
```

### States

| State | When rendered |
|---|---|
| `loading` | Initial fetch in progress |
| `locked` | `escrow_status` is not `funds_released` |
| `form` | Ready to review — escrow released, not yet reviewed |
| `submitting` | POST in flight |
| `submitted` | Review saved; waiting for peer |
| `revealed` | Both reviews in; shows peer's review + AI summary |
| `error` | Network / DB error on mount |

---

## TradesmanRating — React component

### Files

| File | Purpose |
|---|---|
| `frontend/components/TradesmanRating.jsx` | Source (keep in sync) |
| `backend/static/components/TradesmanRating.jsx` | Deployed copy served by FastAPI |

### What it does

A purpose-built React component (no external UI library) for the full 5-star review submission flow:

- **Escrow gate** — Submit button is `disabled` unless `escrowStatus` is `'released'` or `'funds_released'`; if neither, a locked placeholder is rendered instead of the form
- **Three star-rating rows** — Quality, Communication, Cleanliness — each with hover effects and a label (`Poor` → `Excellent`)
- **Live overall badge** — updates in real-time as the user adjusts sub-ratings (matches the DB `GENERATED` column)
- **Feedback textarea** — optional, 2000-char limit with live counter
- **Private feedback field** — visually distinct (amber/dashed border), clearly labelled "Admin only — not shown to the tradesman". Sent as `private_feedback` in the INSERT payload; excluded from `visible_reviews`
- **Structured Supabase insert** via `@supabase/supabase-js` client passed as prop

### Props

| Prop | Type | Required | Description |
|---|---|---|---|
| `supabase` | SupabaseClient | ✓ | Pre-initialised `@supabase/supabase-js` client |
| `jobId` | string | ✓ | Job UUID (transaction anchor) |
| `reviewerId` | string | ✓ | Current user's UUID |
| `revieweeId` | string | ✓ | The tradesman's UUID |
| `reviewerRole` | `'client'`\|`'contractor'` | | Defaults to `'client'` |
| `revieweeRole` | `'client'`\|`'contractor'` | | Defaults to `'contractor'` |
| `revieweeName` | string | | Display name shown in the UI |
| `escrowStatus` | string | ✓ | From your payment layer. Accepts `'released'` or `'funds_released'` |
| `onSuccess` | `(review) => void` | | Called with the saved row on success |
| `onError` | `(error) => void` | | Called on Supabase insert error |

### Installation

```bash
npm install @supabase/supabase-js react react-dom
```

### Usage

```jsx
import { createClient } from '@supabase/supabase-js'
import TradesmanRating from './components/TradesmanRating'

const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  global: { headers: { Authorization: `Bearer ${session.access_token}` } },
})

function JobCompletedPage({ job, currentUser, tradesman }) {
  return (
    <TradesmanRating
      supabase={supabase}
      jobId={job.id}
      reviewerId={currentUser.id}
      revieweeId={tradesman.id}
      reviewerRole="client"
      revieweeRole="contractor"
      revieweeName={tradesman.business_name}
      escrowStatus={job.escrow_status}       // 'funds_released' unlocks the form
      onSuccess={(saved) => console.log('Review saved:', saved)}
    />
  )
}
```

### Private feedback — admin access

The `private_feedback` field is sent in the same INSERT as the rest of the review but is **never returned by `visible_reviews`**. To read it, use the service role key in your admin tooling:

```js
// Admin API (service role — bypasses RLS)
const { data } = await adminSupabase
  .from('reviews')
  .select('id, reviewer_id, private_feedback, submitted_at')
  .not('private_feedback', 'is', null)
  .order('submitted_at', { ascending: false })
```

---

## Row Level Security

> **Live DB note:** The policies below are for the designed schema (migration 016). The live DB uses the service-role client in `reviews.py` to bypass RLS for all writes, with Python-level ownership checks (`reviewer_id = auth.uid()`).

| Policy | Who | What |
|---|---|---|
| `reviews: insert own` | Authenticated | Can insert only if `reviewer_id = auth.uid()` |
| `reviews: select own submission` | Reviewer | Can always read their own review (before and after reveal); `USING (auth.uid() = reviewer_id)` |
| `reviews: select revealed about me` [Designed] | Reviewee | Can read reviews about them only after `content_visible = TRUE` or `reveal_at <= NOW()`; `USING (auth.uid() = reviewee_id AND (content_visible OR reveal_at <= NOW()))` |
| *(no UPDATE policy)* | — | Reviews cannot be edited after submission |
| `DELETE /reviews/{id}` (soft-delete) [Live] | Reviewer | Stamps `deleted_at` + `deleted_by_user_id`; row retained for audit; Python checks `reviewer_id = auth.uid()` before allowing |

> **Migration 016** hardened this by dropping any auto-generated `USING (true)` policies that Supabase's dashboard may have created. Both correct SELECT policies are recreated in a clean state and the column-level `REVOKE SELECT (private_feedback)` is re-asserted to remain effective even if a future dashboard action adds a broad table-level grant.

Service-role / admin access bypasses RLS as normal (e.g. for moderation or the sentiment Edge Function).

---

## Rating helper functions

```sql
-- Average overall rating a contractor has received (from clients)
SELECT public.contractor_rating('contractor-uuid-here');
-- → 4.33   (average of generated rating column across revealed reviews)

-- Average overall rating a client has received (from contractors)
SELECT public.client_rating('client-uuid-here');
-- → 3.67
```

Both functions return `NULL` if the user has no revealed reviews yet. They only count **revealed** reviews.

### Per-dimension breakdown for a profile page

```sql
SELECT
    ROUND(AVG(rating_quality),       2) AS avg_quality,
    ROUND(AVG(rating_communication), 2) AS avg_communication,
    ROUND(AVG(rating_cleanliness),   2) AS avg_cleanliness,
    ROUND(AVG(rating),               2) AS avg_overall,
    COUNT(*)                            AS review_count
FROM visible_reviews
WHERE reviewee_id   = $contractor_id
  AND reviewee_role = 'contractor';
```

---

## How to submit a review (app flow)

### 1. Check the job is reviewable

```sql
SELECT id, status, escrow_status
FROM   jobs
WHERE  id            = $job_id
  AND  escrow_status = 'funds_released';
```

### 2. Determine the reviewer's role

```sql
-- Is the current user the client?
SELECT EXISTS (SELECT 1 FROM jobs WHERE id = $job_id AND user_id = auth.uid());

-- Is the current user the contractor?
SELECT EXISTS (
    SELECT 1 FROM bids
    WHERE  job_id        = $job_id
      AND  contractor_id = auth.uid()
      AND  status        = 'accepted'
);
```

### 3. Insert the review — live schema [Live]

Use `POST /reviews` (requires JWT) or insert directly using the live column names:

```sql
-- Client reviewing the contractor (live schema)
INSERT INTO reviews (
    job_id, contractor_id, reviewer_id,
    rating_quality, rating_communication, rating_cleanliness,
    comment,
    private_feedback      -- optional; admin-only, never returned by the API
) VALUES (
    $job_id, $contractor_id, $client_id,
    5, 4, 5,
    'Excellent work, arrived on time and left the site clean.',
    NULL
);
```

> **Designed schema** (migrations 005–008) uses `reviewee_id`, `reviewer_role`, `reviewee_role`, and `body` instead. If applying migrations to a fresh DB, use the designed schema fields; if writing against the live DB, use the live schema fields above.
```

The `on_review_submitted` trigger fires automatically. If this is the second review, both are revealed and the job advances to `completed`.

### 4. Read reviews for a contractor profile page

```sql
-- All revealed reviews (private_feedback excluded by the view)
SELECT
    rating_quality, rating_communication, rating_cleanliness,
    rating, body, ai_pros_cons, submitted_at
FROM   visible_reviews
WHERE  reviewee_id   = $contractor_id
  AND  reviewee_role = 'contractor'
ORDER BY submitted_at DESC;

-- Admin: read private feedback (service role only, bypasses RLS)
SELECT id, reviewer_id, private_feedback, submitted_at
FROM   reviews
WHERE  reviewee_id   = $contractor_id
  AND  private_feedback IS NOT NULL
ORDER BY submitted_at DESC;

-- Aggregated AI summary (from contractor_details)
SELECT ai_review_summary
FROM   contractor_details
WHERE  id = $contractor_id;
```

---

## Future work & payment integration

### Connecting escrow / payments

When the payment layer is built, the complete handoff is:

```
Payment provider confirms release
        │
        ▼
UPDATE jobs
   SET status        = 'awaiting_review',
       escrow_status = 'funds_released'
 WHERE id = $job_id;
        │
        ▼
Both parties receive a notification
        │
        ▼
ReviewMediator unlocks (escrow_status gate passes)
        │
        ▼
on_review_submitted trigger fires twice → completed
```

### Suggested future enhancements

| Enhancement | Notes |
|---|---|
| **Dispute / moderation flag** | Add `flagged BOOLEAN` + `flagged_reason TEXT` to `reviews`; admin-only RLS UPDATE policy |
| **Review reminder notifications** | Query `reviews` where only one side has reviewed and `reveal_at` is within 48 hours; trigger a push/email via a Supabase scheduled function or pg_cron |
| **Response to a review** | Add `response_body TEXT` + `responded_at TIMESTAMPTZ`; only the reviewee can write it, one-time only |
| **Weighted / recency scoring** | Replace `AVG()` helpers with a weighted function that discounts reviews older than 12 months |
| **Minimum reviews threshold** | Only display `contractor_rating()` publicly once the contractor has ≥ 3 revealed reviews |
| **Sentiment trend on profile** | Show whether the AI summary has improved over time (compare `ai_review_summary.last_updated` snapshots) |
| **Contractor response to AI summary** | Allow the contractor to add a public rebuttal to a specific Con point |
