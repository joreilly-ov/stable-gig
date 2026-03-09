-- ============================================================
-- Migration 001: Initial schema
-- Run once against your Supabase project via the SQL Editor
-- or Supabase CLI: supabase db push
-- ============================================================

-- ── Tables ──────────────────────────────────────────────────

-- profiles: one row per authenticated consumer (auto-created on signup via trigger)
CREATE TABLE IF NOT EXISTS public.profiles (
    id           UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name    TEXT,
    -- US ZIP only: 5-digit or ZIP+4
    postcode     TEXT        CHECK (postcode ~ '^\d{5}(-\d{4})?$'),
    road_address TEXT,
    city         TEXT,
    -- 2-letter US state abbreviation (enforced by application layer as well)
    state        TEXT        CHECK (char_length(state) = 2),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  public.profiles           IS 'Consumer profile; one row per auth.users record.';
COMMENT ON COLUMN public.profiles.postcode  IS 'US ZIP code only — 5-digit or ZIP+4.';
COMMENT ON COLUMN public.profiles.state     IS '2-letter US state abbreviation, e.g. CA.';

-- trades: business accounts — admin-managed, no public self-signup
CREATE TABLE IF NOT EXISTS public.trades (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    business_name   TEXT        NOT NULL,
    trade_category  TEXT        NOT NULL
                                CHECK (trade_category IN (
                                    'plumbing', 'electrical', 'structural',
                                    'damp', 'roofing', 'general'
                                )),
    verified_status BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.trades IS 'Trade businesses. Inserted by service-role admins only.';

-- videos: uploaded videos and their Gemini analysis results
CREATE TABLE IF NOT EXISTS public.videos (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    filename        TEXT        NOT NULL,
    analysis_result JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.videos IS 'Video uploads and Gemini analysis results, keyed to the uploading user.';

-- trades_videos: which trade businesses can see which videos (admin-assigned)
CREATE TABLE IF NOT EXISTS public.trades_videos (
    trade_id    UUID        NOT NULL REFERENCES public.trades(id) ON DELETE CASCADE,
    video_id    UUID        NOT NULL REFERENCES public.videos(id) ON DELETE CASCADE,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trade_id, video_id)
);

-- trades_users: maps individual trade business users to their trade (for RLS)
CREATE TABLE IF NOT EXISTS public.trades_users (
    trade_id UUID NOT NULL REFERENCES public.trades(id) ON DELETE CASCADE,
    user_id  UUID NOT NULL REFERENCES auth.users(id)    ON DELETE CASCADE,
    PRIMARY KEY (trade_id, user_id)
);

-- ── Row Level Security ───────────────────────────────────────

ALTER TABLE public.profiles      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trades        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.videos        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trades_videos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trades_users  ENABLE ROW LEVEL SECURITY;

-- profiles: each user owns exactly their own row
CREATE POLICY "profiles: select own"
    ON public.profiles FOR SELECT
    USING (auth.uid() = id);

CREATE POLICY "profiles: insert own"
    ON public.profiles FOR INSERT
    WITH CHECK (auth.uid() = id);

CREATE POLICY "profiles: update own"
    ON public.profiles FOR UPDATE
    USING (auth.uid() = id);

-- trades: readable only by users who belong to that trade
CREATE POLICY "trades: read by members"
    ON public.trades FOR SELECT
    USING (
        id IN (
            SELECT trade_id FROM public.trades_users WHERE user_id = auth.uid()
        )
    );

-- videos: consumers see only their own uploads
CREATE POLICY "videos: select own"
    ON public.videos FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "videos: insert own"
    ON public.videos FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- trades_videos: trade members can see videos assigned to their trade
CREATE POLICY "trades_videos: trade members can select"
    ON public.trades_videos FOR SELECT
    USING (
        trade_id IN (
            SELECT trade_id FROM public.trades_users WHERE user_id = auth.uid()
        )
    );

-- trades_users: users can see their own membership record
CREATE POLICY "trades_users: select own"
    ON public.trades_users FOR SELECT
    USING (user_id = auth.uid());

-- ── Trigger: auto-create profile row on user signup ─────────

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id)
    VALUES (NEW.id)
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
