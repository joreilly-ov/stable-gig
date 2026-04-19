-- ── 019_cslb_tables.sql ─────────────────────────────────────────────────────
-- California State Licensing Board (CSLB) public data tables.
--
-- Source: CSLB Data Portal bulk export
--   cslb_licences   — master licence records (243,745 rows at last import)
--   cslb_personnel  — associated personnel / officers per licence (404,481 rows)
--
-- Safe to apply multiple times — all CREATE statements use IF NOT EXISTS.
-- Data is loaded via the Python import script (scripts/import_cslb.py).
-- ---------------------------------------------------------------------------


-- ── 1. cslb_licences ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.cslb_licences (
    licence_number          TEXT        PRIMARY KEY,

    -- Identity
    last_update             DATE,
    business_name           TEXT        NOT NULL,
    business_name_2         TEXT,                       -- DBA / secondary name
    full_business_name      TEXT,                       -- used for sole-owner full names
    name_type               TEXT,                       -- NAME-TP-2: "Current Name" etc.

    -- Address
    mailing_address         TEXT,
    city                    TEXT,
    state                   TEXT,
    county                  TEXT,
    zip_code                TEXT,
    country                 TEXT,

    -- Contact & business
    business_phone          TEXT,
    business_type           TEXT,                       -- Sole Owner | Corporation | Partnership | Limited Liability | JointVenture

    -- Licence lifecycle dates
    issue_date              DATE,
    reissue_date            DATE,
    expiration_date         DATE,
    inactivation_date       DATE,
    reactivation_date       DATE,

    -- Pending actions
    pending_suspension      DATE,
    pending_class_removal   DATE,

    -- Status
    primary_status          TEXT        NOT NULL,       -- CLEAR | Work Comp Susp | Judgement Susp | BOND Pay Susp | ...
    secondary_status        TEXT,
    classifications         TEXT,                       -- e.g. "C57", "B", "C-10|C-36"

    -- Asbestos
    asbestos_reg            TEXT,

    -- Workers Compensation
    wc_coverage_type        TEXT,                       -- Exempt | Workers' Compensation Insurance | Self-Insured | Family | Out of State | License does not have current W/C
    wc_insurance_company    TEXT,
    wc_policy_number        TEXT,
    wc_effective_date       DATE,
    wc_expiration_date      DATE,
    wc_cancellation_date    DATE,
    wc_suspend_date         DATE,

    -- Contractor Bond (CB) — present on ~99.9% of licences
    cb_surety_company       TEXT,
    cb_number               TEXT,
    cb_effective_date       DATE,
    cb_cancellation_date    DATE,
    cb_amount               INTEGER,

    -- Workers / Judgment Bond (WB)
    wb_surety_company       TEXT,
    wb_number               TEXT,
    wb_effective_date       DATE,
    wb_cancellation_date    DATE,
    wb_amount               INTEGER,

    -- Disciplinary Bond (DB) — sparse (~1.8k rows)
    db_surety_company       TEXT,
    db_number               TEXT,
    db_effective_date       DATE,
    db_cancellation_date    DATE,
    db_amount               INTEGER,
    db_date_required        DATE,
    db_discp_case_region    TEXT,
    db_bond_reason          TEXT,
    db_case_no              TEXT,

    -- Housekeeping
    imported_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.cslb_licences IS
    'California State Licensing Board master licence records. Refreshed periodically via import_cslb.py.';

-- Indexes for common lookup patterns
CREATE INDEX IF NOT EXISTS cslb_licences_primary_status_idx
    ON public.cslb_licences (primary_status);

CREATE INDEX IF NOT EXISTS cslb_licences_expiration_date_idx
    ON public.cslb_licences (expiration_date);

CREATE INDEX IF NOT EXISTS cslb_licences_business_name_idx
    ON public.cslb_licences USING gin (to_tsvector('english', business_name));


-- ── 2. cslb_personnel ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.cslb_personnel (
    -- Natural composite PK: one row per (licence, CSLB sequence number)
    licence_number          TEXT        NOT NULL REFERENCES public.cslb_licences(licence_number) ON DELETE CASCADE,
    seq_no                  TEXT        NOT NULL,
    PRIMARY KEY (licence_number, seq_no),

    last_updated            DATE,
    record_type             TEXT,                       -- always "Class/Title"
    name_type               TEXT,                       -- Principal | Business | Principal|AKA
    name                    TEXT        NOT NULL,       -- trimmed on import

    -- Pipe-separated multi-role fields stored as arrays
    titles                  TEXT[],                     -- EMP-Titl-CDE split on '|'
    class_codes             TEXT[],                     -- CL-CDE split on '|'
    class_code_statuses     TEXT[],                     -- CL-CDE-STAT split on '|'
    association_dates       TEXT[],                     -- ASSN-DT split on '|' (kept as text)
    disassociation_dates    TEXT[],                     -- DIS-ASSN-DT split on '|'

    -- Qualifier / surety bond (sparse)
    surety_type             TEXT,
    surety_company          TEXT,
    bond_number             TEXT,
    bond_amount             INTEGER,
    bond_effective_date     DATE,
    bond_cancellation_date  DATE,

    -- Housekeeping
    imported_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.cslb_personnel IS
    'CSLB personnel and officers per licence. Pipe-separated multi-role fields are stored as arrays.';

CREATE INDEX IF NOT EXISTS cslb_personnel_name_idx
    ON public.cslb_personnel (name);
