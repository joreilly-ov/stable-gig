-- ============================================================
-- Migration 006: Categorical Ratings, Escrow Status & AI Summary
--
-- Changes:
--   1. jobs          — add escrow_status column (payment layer hook)
--   2. reviews       — replace single rating with three sub-ratings
--                      (Cleanliness, Communication, Accuracy);
--                      overall rating becomes a generated column
--   3. reviews       — add ai_pros_cons JSONB (filled by Edge Function)
--   4. contractor_details — add ai_review_summary JSONB
--                           (aggregated profile-level summary)
--   5. Update contractor_rating() / client_rating() helpers so they
--      continue to work with the new generated rating column.
-- ============================================================


-- ── 1. escrow_status on jobs ──────────────────────────────────────
--
-- The payment / escrow layer writes to this column.
-- ReviewMediator gates rendering on escrow_status = 'funds_released'.
--
-- Lifecycle (managed externally by the payment service):
--   pending → held → funds_released | refunded

ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS escrow_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (escrow_status IN ('pending', 'held', 'funds_released', 'refunded'));

COMMENT ON COLUMN public.jobs.escrow_status IS
    'Payment escrow state. ReviewMediator only activates once funds_released.';


-- ── 2. Replace the single rating with three categorical sub-ratings ──
--
-- The original rating column (SMALLINT, NOT NULL) is dropped and
-- replaced with three explicit sub-ratings.  The overall rating is
-- re-added as a GENERATED column (average of the three sub-ratings)
-- so all existing queries on reviews.rating continue to work.
--
-- Categories:
--   rating_cleanliness    — how clean was the work area / property access?
--   rating_communication  — how well did the other party communicate?
--   rating_accuracy       — accuracy of quote vs. final cost (client→contractor)
--                           or accuracy of job description (contractor→client)

-- Drop the existing raw rating column
ALTER TABLE public.reviews
    DROP COLUMN IF EXISTS rating;

-- Add the three categorical sub-ratings (all required)
ALTER TABLE public.reviews
    ADD COLUMN rating_cleanliness   SMALLINT NOT NULL DEFAULT 3
        CHECK (rating_cleanliness   BETWEEN 1 AND 5),
    ADD COLUMN rating_communication SMALLINT NOT NULL DEFAULT 3
        CHECK (rating_communication BETWEEN 1 AND 5),
    ADD COLUMN rating_accuracy      SMALLINT NOT NULL DEFAULT 3
        CHECK (rating_accuracy      BETWEEN 1 AND 5);

-- Remove the defaults now that the column exists (enforce explicit values at insert time)
ALTER TABLE public.reviews
    ALTER COLUMN rating_cleanliness   DROP DEFAULT,
    ALTER COLUMN rating_communication DROP DEFAULT,
    ALTER COLUMN rating_accuracy      DROP DEFAULT;

-- Re-add rating as a generated column (average of the three, rounded to 2dp)
ALTER TABLE public.reviews
    ADD COLUMN rating NUMERIC(3,2) GENERATED ALWAYS AS (
        ROUND(
            (rating_cleanliness::numeric + rating_communication::numeric + rating_accuracy::numeric) / 3,
            2
        )
    ) STORED;

COMMENT ON COLUMN public.reviews.rating_cleanliness   IS '1–5: cleanliness of work area (client→contractor) or property access (contractor→client).';
COMMENT ON COLUMN public.reviews.rating_communication IS '1–5: quality of communication from the other party.';
COMMENT ON COLUMN public.reviews.rating_accuracy      IS '1–5: accuracy of quote vs. final cost, or job description vs. actual work needed.';
COMMENT ON COLUMN public.reviews.rating               IS 'Generated: average of the three sub-ratings (read-only).';


-- ── 3. AI pros/cons on reviews ────────────────────────────────────
--
-- Populated asynchronously by the review-sentiment Edge Function
-- after a review is submitted.
--
-- Expected shape:
--   {
--     "pros":             ["string", ...],   -- max 3
--     "cons":             ["string", ...],   -- max 3
--     "one_line_summary": "string"
--   }

ALTER TABLE public.reviews
    ADD COLUMN IF NOT EXISTS ai_pros_cons JSONB;

COMMENT ON COLUMN public.reviews.ai_pros_cons IS
    'AI-generated pros/cons extracted from the review body. Populated by the review-sentiment Edge Function.';


-- ── 4. Aggregated AI summary on contractor_details ────────────────
--
-- The review-sentiment Edge Function may optionally refresh this
-- after each new revealed review, providing a profile-level summary
-- of what clients consistently praise or flag about a contractor.
--
-- Expected shape:
--   {
--     "top_pros": ["string", ...],
--     "top_cons": ["string", ...],
--     "last_updated": "ISO timestamp"
--   }

ALTER TABLE public.contractor_details
    ADD COLUMN IF NOT EXISTS ai_review_summary JSONB;

COMMENT ON COLUMN public.contractor_details.ai_review_summary IS
    'Aggregated AI pros/cons across all revealed reviews. Refreshed by the review-sentiment Edge Function.';


-- ── 5. Update aggregate rating helpers ───────────────────────────
--
-- The generated rating column is NUMERIC(3,2) not SMALLINT.
-- AVG() still works; just re-create with the same signatures
-- so the return type stays NUMERIC.

CREATE OR REPLACE FUNCTION public.contractor_rating(p_contractor_id UUID)
RETURNS NUMERIC
LANGUAGE sql
STABLE
AS $$
    SELECT ROUND(AVG(rating)::NUMERIC, 2)
    FROM   public.reviews
    WHERE  reviewee_id   = p_contractor_id
      AND  reviewee_role = 'contractor'
      AND  (content_visible OR reveal_at <= NOW());
$$;

CREATE OR REPLACE FUNCTION public.client_rating(p_client_id UUID)
RETURNS NUMERIC
LANGUAGE sql
STABLE
AS $$
    SELECT ROUND(AVG(rating)::NUMERIC, 2)
    FROM   public.reviews
    WHERE  reviewee_id   = p_client_id
      AND  reviewee_role = 'client'
      AND  (content_visible OR reveal_at <= NOW());
$$;


-- ── 6. Refresh visible_reviews view ──────────────────────────────
--
-- Recreate so it includes the new columns.

DROP VIEW IF EXISTS public.visible_reviews;

CREATE VIEW public.visible_reviews AS
SELECT
    id,
    job_id,
    reviewer_id,
    reviewee_id,
    reviewer_role,
    reviewee_role,
    rating_cleanliness,
    rating_communication,
    rating_accuracy,
    rating,                                            -- generated average
    CASE
        WHEN content_visible OR reveal_at <= NOW() THEN body
        ELSE NULL
    END                                            AS body,
    CASE
        WHEN content_visible OR reveal_at <= NOW() THEN ai_pros_cons
        ELSE NULL
    END                                            AS ai_pros_cons,   -- also hidden until revealed
    content_visible OR (reveal_at <= NOW())        AS is_revealed,
    submitted_at,
    reveal_at
FROM public.reviews;

COMMENT ON VIEW public.visible_reviews IS
    'Reviews with double-blind enforced. body and ai_pros_cons are NULL until the peer reviews or the 14-day timer expires.';
