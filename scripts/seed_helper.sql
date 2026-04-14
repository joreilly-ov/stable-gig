-- Run this once in the Supabase SQL Editor before running seed_data.py.
--
-- Creates two helper functions used by the seed script to bypass
-- PostgREST schema cache issues with ARRAY and generated columns.

-- ── 1. Insert contractor ─────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.seed_insert_contractor(
    p_user_id           UUID,
    p_business_name     TEXT,
    p_postcode          TEXT,
    p_phone             TEXT,
    p_expertise         TEXT[],
    p_license_number    TEXT DEFAULT NULL,
    p_insurance_details TEXT DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_id UUID;
BEGIN
    -- Return existing contractor if already seeded
    SELECT id INTO v_id FROM public.contractors WHERE user_id = p_user_id LIMIT 1;
    IF v_id IS NOT NULL THEN
        RETURN v_id;
    END IF;

    INSERT INTO public.contractors (user_id, business_name, postcode, phone, expertise, license_number, insurance_details)
    VALUES (p_user_id, p_business_name, p_postcode, p_phone, p_expertise, p_license_number, p_insurance_details)
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;


-- ── 2. Insert review ─────────────────────────────────────────────────────
-- Omits the generated "overall" column — Postgres computes it automatically.

CREATE OR REPLACE FUNCTION public.seed_insert_review(
    p_contractor_id        UUID,
    p_job_id               TEXT,
    p_reviewer_id          UUID,
    p_rating_quality       SMALLINT,
    p_rating_communication SMALLINT,
    p_rating_cleanliness   SMALLINT,
    p_comment              TEXT
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.reviews (
        contractor_id, job_id, reviewer_id,
        rating_quality, rating_communication, rating_cleanliness,
        comment
    ) VALUES (
        p_contractor_id, p_job_id, p_reviewer_id,
        p_rating_quality, p_rating_communication, p_rating_cleanliness,
        p_comment
    );
END;
$$;
