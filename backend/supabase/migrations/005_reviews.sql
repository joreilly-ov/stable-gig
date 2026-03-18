-- ============================================================
-- Migration 005: Reviews
--
-- Consumers can leave one review per completed job, rating the
-- contractor across five categories plus an overall score.
--
-- reviews
--   └─ job_id        → jobs.id
--   └─ contractor_id → contractors.id
--   └─ reviewer_id   → auth.users.id
-- ============================================================

CREATE TABLE public.reviews (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID        NOT NULL REFERENCES public.jobs(id)        ON DELETE CASCADE,
    contractor_id   UUID        NOT NULL REFERENCES public.contractors(id) ON DELETE CASCADE,
    reviewer_id     UUID        NOT NULL REFERENCES auth.users(id)         ON DELETE CASCADE,

    -- Categorical ratings (1 = poor … 5 = excellent)
    quality         SMALLINT    NOT NULL CHECK (quality       BETWEEN 1 AND 5),
    timeliness      SMALLINT    NOT NULL CHECK (timeliness    BETWEEN 1 AND 5),
    communication   SMALLINT    NOT NULL CHECK (communication BETWEEN 1 AND 5),
    value           SMALLINT    NOT NULL CHECK (value         BETWEEN 1 AND 5),
    tidiness        SMALLINT    NOT NULL CHECK (tidiness      BETWEEN 1 AND 5),

    -- Headline score
    overall         SMALLINT    NOT NULL CHECK (overall       BETWEEN 1 AND 5),

    comment         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One review per job per reviewer
    UNIQUE (job_id, reviewer_id)
);

COMMENT ON TABLE  public.reviews                IS 'Post-job reviews left by consumers about contractors.';
COMMENT ON COLUMN public.reviews.overall        IS 'Headline score 1–5 chosen by the reviewer.';
COMMENT ON COLUMN public.reviews.quality        IS 'Quality of workmanship (1–5).';
COMMENT ON COLUMN public.reviews.timeliness     IS 'On-time start and completion (1–5).';
COMMENT ON COLUMN public.reviews.communication  IS 'Kept the customer informed (1–5).';
COMMENT ON COLUMN public.reviews.value          IS 'Value for money (1–5).';
COMMENT ON COLUMN public.reviews.tidiness       IS 'Left the site clean and tidy (1–5).';

-- ── Indexes ───────────────────────────────────────────────────

CREATE INDEX reviews_contractor_idx ON public.reviews (contractor_id, created_at DESC);
CREATE INDEX reviews_job_idx        ON public.reviews (job_id);

-- ── Row Level Security ────────────────────────────────────────

ALTER TABLE public.reviews ENABLE ROW LEVEL SECURITY;

-- Anyone authenticated can read reviews (public profile data)
CREATE POLICY "reviews: authenticated read"
    ON public.reviews FOR SELECT
    USING (auth.role() = 'authenticated');

-- Reviewers manage their own reviews
CREATE POLICY "reviews: reviewer insert"
    ON public.reviews FOR INSERT
    WITH CHECK (auth.uid() = reviewer_id);

CREATE POLICY "reviews: reviewer delete"
    ON public.reviews FOR DELETE
    USING (auth.uid() = reviewer_id);
