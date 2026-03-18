# Marketplace Rating & Review System

## Overview

The review system is modelled on platforms like Uber and Upwork: **both parties rate each other after a job completes**, and reviews are **tied to a real transaction** (the `job_id`) so fake reviews are structurally impossible.

It is a **double-blind** system — neither party can see what the other wrote until both have submitted, or a 14-day fallback timer expires. This prevents scores being influenced by the other person's review.

---

## Core design principles

| Principle | How it is enforced |
|---|---|
| **Transaction-anchored** | Every review references a `job_id`. No job = no review. |
| **One review per party per job** | `UNIQUE (job_id, reviewer_id)` database constraint. |
| **Double-blind** | `content_visible = FALSE` by default; trigger reveals both reviews simultaneously when the second is submitted. |
| **Immutable** | No `UPDATE` or `DELETE` RLS policies — reviews cannot be edited after submission. |
| **Bidirectional** | Client rates contractor; contractor rates client. Both ratings live in the same `reviews` table. |

---

## Database schema

### Migration file
`backend/supabase/migrations/005_rating_system.sql`

### Tables & objects added

| Object | Type | Purpose |
|---|---|---|
| `reviews` | Table | Stores all reviews for both parties |
| `visible_reviews` | View | Query-time double-blind enforcement (use this in the app, not the raw table) |
| `handle_review_submitted()` | Trigger function | Reveals both reviews when the second one is submitted |
| `on_review_submitted` | Trigger | Fires `AFTER INSERT ON reviews` |
| `contractor_rating(uuid)` | SQL function | Returns a contractor's average revealed rating |
| `client_rating(uuid)` | SQL function | Returns a client's average revealed rating |

---

## Job status lifecycle

Reviews can only be submitted when a job reaches `awaiting_review`. The full lifecycle:

```
open → awarded → in_progress → awaiting_review → completed | cancelled
```

| Status | Meaning |
|---|---|
| `open` | Job posted, accepting bids |
| `awarded` | A bid has been accepted |
| `in_progress` | Work has started |
| `awaiting_review` | Work is done / escrow released — both parties are prompted to review |
| `completed` | Both reviews submitted (trigger advances this automatically) |
| `cancelled` | Job cancelled at any stage |

> **Payment integration note:** `awaiting_review` is the natural handoff point from the payment/escrow layer. When escrow is released, set `jobs.status = 'awaiting_review'`. The review system takes over from there.

---

## The `reviews` table

```sql
reviews (
    id              UUID        PRIMARY KEY
    job_id          UUID        → jobs.id          -- transaction anchor
    reviewer_id     UUID        → auth.users.id    -- who wrote this review
    reviewee_id     UUID        → auth.users.id    -- who is being reviewed
    reviewer_role   TEXT        'client' | 'contractor'
    reviewee_role   TEXT        'client' | 'contractor'
    rating          SMALLINT    1–5
    body            TEXT        free-text (hidden until revealed)
    content_visible BOOLEAN     FALSE until peer reviews or timer expires
    reveal_at       TIMESTAMPTZ submitted_at + 14 days (fallback)
    submitted_at    TIMESTAMPTZ
)
```

### Identity mapping

Because the codebase uses the **Clean Split** design (`contractors.id = profiles.id = auth.users.id`), both the client and contractor are identified by their `auth.users` UUID. No separate FK columns are needed — `reviewer_role` / `reviewee_role` tell you which side is which.

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

The raw `reviews` table contains hidden body text. **All application queries should use the `visible_reviews` view**, which enforces the double-blind automatically.

```sql
-- Correct
SELECT * FROM visible_reviews WHERE reviewee_id = $1;

-- Wrong — exposes hidden content
SELECT * FROM reviews WHERE reviewee_id = $1;
```

---

## Row Level Security

| Policy | Who | What |
|---|---|---|
| `reviews: insert own` | Anyone | Can insert only if `reviewer_id = auth.uid()` |
| `reviews: select own submission` | Reviewer | Can always read their own review (before and after reveal) |
| `reviews: select revealed about me` | Reviewee | Can read reviews about them only after `content_visible = TRUE` or `reveal_at` has passed |
| *(no UPDATE policy)* | — | Reviews cannot be edited after submission |
| *(no DELETE policy)* | — | Reviews cannot be deleted by users |

Service-role / admin access bypasses RLS as normal (e.g. for moderation).

---

## Rating helper functions

```sql
-- Average rating a contractor has received (from clients)
SELECT public.contractor_rating('contractor-uuid-here');
-- → 4.75

-- Average rating a client has received (from contractors)
SELECT public.client_rating('client-uuid-here');
-- → 3.50
```

Both functions return `NULL` if the user has no revealed reviews yet. They only count **revealed** reviews, so scores are not skewed by hidden reviews that haven't been mutually committed.

---

## How to submit a review (app flow)

### 1. Check the job is reviewable

```sql
SELECT id, status FROM jobs WHERE id = $job_id AND status = 'awaiting_review';
```

### 2. Determine the reviewer's role

```sql
-- Is the current user the client?
SELECT EXISTS (SELECT 1 FROM jobs WHERE id = $job_id AND user_id = auth.uid());

-- Is the current user the contractor?
SELECT EXISTS (SELECT 1 FROM bids WHERE job_id = $job_id AND contractor_id = auth.uid() AND status = 'accepted');
```

### 3. Insert the review

```sql
-- Client reviewing the contractor
INSERT INTO reviews (job_id, reviewer_id, reviewee_id, reviewer_role, reviewee_role, rating, body)
VALUES (
    $job_id,
    $client_id,       -- auth.uid()
    $contractor_id,   -- from the accepted bid
    'client',
    'contractor',
    5,
    'Excellent work, arrived on time and left the site clean.'
);

-- Contractor reviewing the client
INSERT INTO reviews (job_id, reviewer_id, reviewee_id, reviewer_role, reviewee_role, rating, body)
VALUES (
    $job_id,
    $contractor_id,   -- auth.uid()
    $client_id,       -- from jobs.user_id
    'contractor',
    'client',
    4,
    'Clear brief, paid promptly, easy to work with.'
);
```

The trigger fires automatically on insert. If this is the second review, both are revealed and the job is marked `completed`.

### 4. Read reviews for a profile page

```sql
-- All revealed reviews about a contractor
SELECT rating, body, reviewer_role, submitted_at
FROM   visible_reviews
WHERE  reviewee_id   = $contractor_id
  AND  reviewee_role = 'contractor'
ORDER BY submitted_at DESC;

-- Summary stat
SELECT public.contractor_rating($contractor_id) AS avg_rating;
```

---

## Future work & payment integration

The review system is deliberately decoupled from payment but designed to slot in cleanly.

### Connecting escrow / payments

When the payment layer is built, the handoff point is the job status transition:

```
Payment / escrow released
        │
        ▼
UPDATE jobs SET status = 'awaiting_review' WHERE id = $job_id;
        │
        ▼
Both parties are notified to leave a review
        │
        ▼
handle_review_submitted() trigger advances to 'completed'
```

### Suggested future enhancements

| Enhancement | Notes |
|---|---|
| **Dispute / moderation flag** | Add a `flagged` boolean + `flagged_reason` text column to `reviews`; admin RLS policy to update it |
| **Review reminder notifications** | Query `reviews` for jobs in `awaiting_review` where only one side has reviewed and `reveal_at` is approaching; send push/email reminders |
| **Response to a review** | Add a `response_body` text + `responded_at` column; only the reviewee can write it, one-time only |
| **Weighted / recency scoring** | Replace the simple `AVG()` helpers with a weighted function that discounts older reviews |
| **Per-category ratings** | Add `JSONB` column (e.g. `{quality: 5, punctuality: 4, communication: 5}`) alongside the single `rating` for richer profiles |
| **Minimum reviews threshold** | Only display `contractor_rating()` publicly once the contractor has ≥ 3 revealed reviews |
