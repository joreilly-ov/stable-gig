-- ============================================================
-- Migration 003: Contractor onboarding
-- Tables: jobs, contractors, contractor_profiles, bids
-- ============================================================

-- ── jobs ─────────────────────────────────────────────────────
-- Jobs posted by consumers (homeowners) seeking tradespeople.

CREATE TABLE IF NOT EXISTS public.jobs (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title        TEXT        NOT NULL,
    description  TEXT        NOT NULL,
    activity     TEXT        NOT NULL
                             CHECK (activity IN (
                                 'plumbing', 'electrical', 'structural',
                                 'damp', 'roofing', 'carpentry', 'painting',
                                 'tiling', 'flooring', 'heating_hvac',
                                 'glazing', 'landscaping', 'general'
                             )),
    postcode     TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'awarded', 'completed', 'cancelled')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.jobs          IS 'Home-repair jobs posted by consumers.';
COMMENT ON COLUMN public.jobs.activity IS 'Trade category required for the job.';
COMMENT ON COLUMN public.jobs.status   IS 'open → awarded → completed | cancelled.';

-- ── contractors ──────────────────────────────────────────────
-- Self-registered contractor businesses.

CREATE TABLE IF NOT EXISTS public.contractors (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    business_name TEXT        NOT NULL,
    postcode      TEXT        NOT NULL,
    phone         TEXT        NOT NULL,
    -- Array of trade activities this contractor offers, e.g. '{"plumbing","roofing"}'
    activities    TEXT[]      NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id)
);

COMMENT ON TABLE  public.contractors            IS 'Contractor business accounts; one row per registered tradesperson.';
COMMENT ON COLUMN public.contractors.activities IS 'Subset of the canonical activity list that this contractor covers.';

-- ── contractor_profiles ───────────────────────────────────────
-- Extended / verified info for a contractor (1-to-1 with contractors).

CREATE TABLE IF NOT EXISTS public.contractor_profiles (
    id                  UUID    PRIMARY KEY REFERENCES public.contractors(id) ON DELETE CASCADE,
    license_number      TEXT,
    insurance_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    years_experience    INTEGER CHECK (years_experience >= 0),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.contractor_profiles                     IS 'Extended contractor details; one row per contractors record.';
COMMENT ON COLUMN public.contractor_profiles.insurance_verified  IS 'Set to TRUE by admin after insurance documents are reviewed.';
COMMENT ON COLUMN public.contractor_profiles.years_experience    IS 'Self-reported years in trade; must be >= 0.';

-- ── bids ─────────────────────────────────────────────────────
-- A contractor submitting a quote/bid for an open job.

CREATE TABLE IF NOT EXISTS public.bids (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id        UUID        NOT NULL REFERENCES public.jobs(id)        ON DELETE CASCADE,
    contractor_id UUID        NOT NULL REFERENCES public.contractors(id) ON DELETE CASCADE,
    amount_pence  INTEGER     NOT NULL CHECK (amount_pence > 0),
    note          TEXT,
    status        TEXT        NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'accepted', 'rejected')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, contractor_id)
);

COMMENT ON TABLE  public.bids              IS 'Contractor bids on consumer jobs.';
COMMENT ON COLUMN public.bids.amount_pence IS 'Quote amount in pence (integer avoids floating-point issues).';
COMMENT ON COLUMN public.bids.status       IS 'pending → accepted | rejected.';

-- ── Row Level Security ────────────────────────────────────────

ALTER TABLE public.jobs                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.contractors          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.contractor_profiles  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bids                 ENABLE ROW LEVEL SECURITY;

-- jobs: consumers manage their own; contractors can read open jobs
CREATE POLICY "jobs: owner full access"
    ON public.jobs FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "jobs: contractors can read open"
    ON public.jobs FOR SELECT
    USING (
        status = 'open'
        AND auth.uid() IN (SELECT user_id FROM public.contractors)
    );

-- contractors: each contractor owns exactly their own row
CREATE POLICY "contractors: select own"
    ON public.contractors FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "contractors: insert own"
    ON public.contractors FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "contractors: update own"
    ON public.contractors FOR UPDATE
    USING (auth.uid() = user_id);

-- contractor_profiles: readable/writable by the owning contractor
CREATE POLICY "contractor_profiles: select own"
    ON public.contractor_profiles FOR SELECT
    USING (
        id IN (SELECT id FROM public.contractors WHERE user_id = auth.uid())
    );

CREATE POLICY "contractor_profiles: insert own"
    ON public.contractor_profiles FOR INSERT
    WITH CHECK (
        id IN (SELECT id FROM public.contractors WHERE user_id = auth.uid())
    );

CREATE POLICY "contractor_profiles: update own"
    ON public.contractor_profiles FOR UPDATE
    USING (
        id IN (SELECT id FROM public.contractors WHERE user_id = auth.uid())
    );

-- bids: contractors manage their own bids; job owners can read bids on their jobs
CREATE POLICY "bids: contractor full access"
    ON public.bids FOR ALL
    USING (
        contractor_id IN (SELECT id FROM public.contractors WHERE user_id = auth.uid())
    )
    WITH CHECK (
        contractor_id IN (SELECT id FROM public.contractors WHERE user_id = auth.uid())
    );

CREATE POLICY "bids: job owner can read"
    ON public.bids FOR SELECT
    USING (
        job_id IN (SELECT id FROM public.jobs WHERE user_id = auth.uid())
    );

-- ── Trigger: auto-update updated_at on contractor_profiles ───

CREATE TRIGGER contractor_profiles_updated_at
    BEFORE UPDATE ON public.contractor_profiles
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ── Trigger: auto-create contractor_profiles row on insert ───

CREATE OR REPLACE FUNCTION public.handle_new_contractor()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.contractor_profiles (id)
    VALUES (NEW.id)
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER on_contractor_created
    AFTER INSERT ON public.contractors
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_contractor();
