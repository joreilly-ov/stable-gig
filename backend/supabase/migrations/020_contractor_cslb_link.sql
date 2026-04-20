-- ── 020_contractor_cslb_link.sql ─────────────────────────────────────────────
-- Adds CSLB verification columns to contractor_details and creates the
-- lookup_cslb_licence() RPC used by the backend to cross-check contractors.
--
-- Prerequisites: migration 019_cslb_tables.sql must be applied first.
-- Safe to apply multiple times — uses IF NOT EXISTS / IF NOT EXIST guards.
-- ---------------------------------------------------------------------------


-- ── 1. Add CSLB columns to contractor_details ────────────────────────────────

ALTER TABLE public.contractor_details
    ADD COLUMN IF NOT EXISTS cslb_licence_number  TEXT
        REFERENCES public.cslb_licences(licence_number) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS licence_verified_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS licence_status        TEXT;

COMMENT ON COLUMN public.contractor_details.cslb_licence_number IS
    'Contractor-supplied CSLB licence number; FK to cslb_licences for cross-checking.';
COMMENT ON COLUMN public.contractor_details.licence_verified_at IS
    'Timestamp of the last successful CSLB lookup for this contractor.';
COMMENT ON COLUMN public.contractor_details.licence_status IS
    'Snapshot of cslb_licences.primary_status at the time of last verification (e.g. CLEAR).';

CREATE INDEX IF NOT EXISTS contractor_details_cslb_licence_idx
    ON public.contractor_details (cslb_licence_number)
    WHERE cslb_licence_number IS NOT NULL;


-- ── 2. RPC: lookup_cslb_licence ──────────────────────────────────────────────
-- Accepts a licence number and returns a JSON object with status, insurance
-- flags, expiry dates, and an array of associated personnel.
--
-- Usage:
--   SELECT lookup_cslb_licence('1000002');
-- or via Supabase client:
--   supabase.rpc('lookup_cslb_licence', {'p_licence_number': '1000002'})

CREATE OR REPLACE FUNCTION public.lookup_cslb_licence(p_licence_number TEXT)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_licence  public.cslb_licences%ROWTYPE;
    v_result   JSONB;
BEGIN
    SELECT * INTO v_licence
    FROM public.cslb_licences
    WHERE licence_number = p_licence_number;

    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    SELECT jsonb_build_object(
        -- Core identity
        'licence_number',       v_licence.licence_number,
        'business_name',        v_licence.business_name,
        'full_business_name',   v_licence.full_business_name,
        'business_type',        v_licence.business_type,
        'classifications',      v_licence.classifications,

        -- Status & dates
        'primary_status',       v_licence.primary_status,
        'secondary_status',     v_licence.secondary_status,
        'issue_date',           v_licence.issue_date,
        'expiration_date',      v_licence.expiration_date,
        'is_active',            (
                                    v_licence.primary_status = 'CLEAR'
                                    AND (v_licence.expiration_date IS NULL OR v_licence.expiration_date >= CURRENT_DATE)
                                ),

        -- Workers' Comp
        'wc_coverage_type',     v_licence.wc_coverage_type,
        'wc_insurer',           v_licence.wc_insurance_company,
        'wc_policy_number',     v_licence.wc_policy_number,
        'wc_expiration_date',   v_licence.wc_expiration_date,
        'wc_is_current',        (
                                    v_licence.wc_coverage_type = 'Exempt'
                                    OR (
                                        v_licence.wc_insurance_company IS NOT NULL
                                        AND v_licence.wc_cancellation_date IS NULL
                                        AND (v_licence.wc_expiration_date IS NULL OR v_licence.wc_expiration_date >= CURRENT_DATE)
                                    )
                                ),

        -- Contractor Bond
        'cb_surety',            v_licence.cb_surety_company,
        'cb_number',            v_licence.cb_number,
        'cb_amount',            v_licence.cb_amount,
        'cb_expiration_date',   v_licence.cb_effective_date,
        'cb_cancellation_date', v_licence.cb_cancellation_date,

        -- Personnel (joined array)
        'personnel',            (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'seq_no',               p.seq_no,
                        'name',                 p.name,
                        'name_type',            p.name_type,
                        'titles',               p.titles,
                        'class_codes',          p.class_codes,
                        'surety_type',          p.surety_type,
                        'surety_company',       p.surety_company,
                        'bond_amount',          p.bond_amount,
                        'bond_effective_date',  p.bond_effective_date,
                        'association_dates',    p.association_dates,
                        'disassociation_dates', p.disassociation_dates
                    )
                    ORDER BY p.seq_no
                ),
                '[]'::jsonb
            )
            FROM public.cslb_personnel p
            WHERE p.licence_number = v_licence.licence_number
        )
    ) INTO v_result;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION public.lookup_cslb_licence(TEXT) IS
    'Returns CSLB licence details plus joined personnel as a single JSONB object. Returns NULL if licence not found.';

-- Grant execute to authenticated users (homeowners can check contractor licences)
GRANT EXECUTE ON FUNCTION public.lookup_cslb_licence(TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION public.lookup_cslb_licence(TEXT) TO anon;
