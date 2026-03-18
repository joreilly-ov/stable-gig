-- ============================================================
-- Migration 005: Marketplace Rating System
--
-- Implements a double-blind, transaction-anchored review system:
--
--   jobs          → extended status lifecycle (adds awaiting_review)
--   reviews       → one review per party per job (job_id is the
--                   escrow/transaction anchor)
--   trigger       → reveals both reviews the moment the second one
--                   lands; 14-day fallback timer for non-responders
--   visible_reviews view → enforces double-blind at query time
--   contractor_rating()  → aggregate helper
--   client_rating()      → aggregate helper
--
-- Identity:
--   client     = jobs.user_id      (profiles / auth.users row)
--   contractor = contractors.id    (mirrors profiles.id / auth.users.id)
-- ============================================================


-- ── 1. Extend jobs.status to include awaiting_review ─────────────
--
-- Previous CHECK: ('open', 'awarded', 'completed', 'cancelled')
-- New lifecycle:   open → awarded → in_progress → awaiting_review
--                      → completed | cancelled

ALTER TABLE public.jobs
    DROP CONSTRAINT IF EXISTS jobs_status_check;

ALTER TABLE public.jobs
    ADD  CONSTRAINT jobs_status_check
         CHECK (status IN (
             'open', 'awarded', 'in_progress',
             'awaiting_review', 'completed', 'cancelled'
         ));

COMMENT ON COLUMN public.jobs.status IS
    'Lifecycle: open → awarded → in_progress → awaiting_review → completed | cancelled.';


-- ── 2. reviews ────────────────────────────────────────────────────
--
-- Design decisions:
--   • job_id is the escrow/transaction anchor — every review must
--     be tied to a real job, preventing fake reviews.
--   • reviewer_id / reviewee_id are both auth.users UUIDs;
--     reviewer_role / reviewee_role ('client' | 'contractor') make
--     the direction explicit without extra joins.
--   • The UNIQUE (job_id, reviewer_id) constraint enforces one
--     review per party per job.
--   • content_visible starts FALSE; the double-blind trigger (step 3)
--     flips it to TRUE when the peer submits, or the visible_reviews
--     view (step 4) falls back to reveal_at for stale non-responders.

CREATE TABLE public.reviews (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- ── Transaction anchor ──────────────────────────────────────
    -- Must reference a job that is in 'awaiting_review' or 'completed'.
    job_id          UUID        NOT NULL
                                REFERENCES public.jobs(id) ON DELETE CASCADE,

    -- ── Parties ─────────────────────────────────────────────────
    -- reviewer_id  = the person writing this review
    -- reviewee_id  = the person being reviewed
    -- Both map to auth.users; no separate client/contractor FK needed
    -- because contractors.id = auth.users.id by the Clean Split design.
    reviewer_id     UUID        NOT NULL
                                REFERENCES auth.users(id) ON DELETE CASCADE,
    reviewee_id     UUID        NOT NULL
                                REFERENCES auth.users(id) ON DELETE CASCADE,

    -- Explicit role labels (avoids re-deriving from the job on every read)
    reviewer_role   TEXT        NOT NULL CHECK (reviewer_role   IN ('client', 'contractor')),
    reviewee_role   TEXT        NOT NULL CHECK (reviewee_role   IN ('client', 'contractor')),

    -- ── Content ─────────────────────────────────────────────────
    rating          SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body            TEXT,                      -- optional free-text; hidden until revealed

    -- ── Double-blind state ───────────────────────────────────────
    -- content_visible = TRUE  → body may be read by the reviewee / public
    -- content_visible = FALSE → body is NULL in visible_reviews until
    --                           the peer submits OR reveal_at passes
    content_visible BOOLEAN     NOT NULL DEFAULT FALSE,
    reveal_at       TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '14 days'),

    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ── Integrity constraints ────────────────────────────────────
    UNIQUE (job_id, reviewer_id),                          -- one review per party per job
    CONSTRAINT reviews_no_self_review  CHECK (reviewer_id <> reviewee_id),
    CONSTRAINT reviews_opposite_roles  CHECK (reviewer_role <> reviewee_role)
);

COMMENT ON TABLE  public.reviews                 IS 'Mutual marketplace reviews; one per party per job.';
COMMENT ON COLUMN public.reviews.job_id          IS 'Escrow/transaction anchor — ties every review to a real job, preventing fake reviews.';
COMMENT ON COLUMN public.reviews.reviewer_role   IS '"client" = homeowner wrote this review; "contractor" = tradesperson wrote it.';
COMMENT ON COLUMN public.reviews.content_visible IS 'FALSE until the peer submits their review or reveal_at passes (double-blind).';
COMMENT ON COLUMN public.reviews.reveal_at       IS '14-day fallback: body becomes readable even if the peer never reviews.';


-- ── 3. Double-blind trigger ───────────────────────────────────────
--
-- Fires AFTER each INSERT on reviews.
--
-- Logic:
--   a) Look for the peer review (same job_id, different reviewer_id).
--   b) If found → flip content_visible = TRUE on BOTH reviews
--      and advance the job to 'completed'.
--   c) If not found → leave content_visible = FALSE.
--      The visible_reviews view (step 4) handles the reveal_at fallback
--      at query time — no cron job or background worker needed.

CREATE OR REPLACE FUNCTION public.handle_review_submitted()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_peer_id UUID;
BEGIN
    -- Find the counterpart review for the same job
    SELECT id
      INTO v_peer_id
      FROM public.reviews
     WHERE job_id      = NEW.job_id
       AND reviewer_id <> NEW.reviewer_id
     LIMIT 1;

    IF v_peer_id IS NOT NULL THEN
        -- Both sides have now reviewed → reveal immediately
        UPDATE public.reviews
           SET content_visible = TRUE
         WHERE id IN (NEW.id, v_peer_id);

        -- Advance job status once both reviews are in
        UPDATE public.jobs
           SET status = 'completed'
         WHERE id     = NEW.job_id
           AND status = 'awaiting_review';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER on_review_submitted
    AFTER INSERT ON public.reviews
    FOR EACH ROW EXECUTE FUNCTION public.handle_review_submitted();


-- ── 4. visible_reviews view — double-blind enforced at query time ─
--
-- Rating is always visible (needed for aggregate stats).
-- body is NULL until:
--   • content_visible = TRUE  (peer has reviewed), OR
--   • reveal_at <= NOW()      (14-day fallback has elapsed)
--
-- Apps should SELECT FROM visible_reviews, not FROM reviews directly,
-- so the double-blind logic is centralised here.

CREATE OR REPLACE VIEW public.visible_reviews AS
SELECT
    id,
    job_id,
    reviewer_id,
    reviewee_id,
    reviewer_role,
    reviewee_role,
    rating,
    CASE
        WHEN content_visible OR reveal_at <= NOW() THEN body
        ELSE NULL
    END                                        AS body,
    content_visible OR (reveal_at <= NOW())    AS is_revealed,
    submitted_at,
    reveal_at
FROM public.reviews;

COMMENT ON VIEW public.visible_reviews IS
    'Reviews with double-blind enforced: body is NULL until the peer reviews or the 14-day timer expires. Rating is always visible.';


-- ── 5. Aggregate rating helpers ───────────────────────────────────
--
-- Only counts revealed reviews (fair — hidden reviews don't skew
-- averages until both parties have committed their honest opinion).

-- Average rating a contractor has received from clients
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

COMMENT ON FUNCTION public.contractor_rating(UUID) IS
    'Returns the average revealed rating for a contractor (NULL if no reviews yet).';

-- Average rating a client has received from contractors
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

COMMENT ON FUNCTION public.client_rating(UUID) IS
    'Returns the average revealed rating for a client (NULL if no reviews yet).';


-- ── 6. Row Level Security ─────────────────────────────────────────

ALTER TABLE public.reviews ENABLE ROW LEVEL SECURITY;

-- Parties can submit their own review (reviewer_id must equal auth.uid())
CREATE POLICY "reviews: insert own"
    ON public.reviews FOR INSERT
    WITH CHECK (auth.uid() = reviewer_id);

-- Reviewers can always read their own submission (before and after reveal)
CREATE POLICY "reviews: select own submission"
    ON public.reviews FOR SELECT
    USING (auth.uid() = reviewer_id);

-- Reviewees can read reviews about them only after the double-blind lifts
CREATE POLICY "reviews: select revealed about me"
    ON public.reviews FOR SELECT
    USING (
        auth.uid() = reviewee_id
        AND (content_visible OR reveal_at <= NOW())
    );

-- No UPDATE or DELETE policies → reviews are immutable once submitted.
-- (Service-role / admin access bypasses RLS as normal.)


-- ── 7. Indexes ────────────────────────────────────────────────────

CREATE INDEX reviews_job_id_idx      ON public.reviews (job_id);
CREATE INDEX reviews_reviewee_id_idx ON public.reviews (reviewee_id);
-- Partial index: only unrevealed rows need reveal_at lookups
CREATE INDEX reviews_reveal_at_idx   ON public.reviews (reveal_at)
    WHERE NOT content_visible;
