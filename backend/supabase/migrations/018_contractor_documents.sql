-- ── 018_contractor_documents.sql ────────────────────────────────────────────
-- Adds the contractor_documents table so contractors can upload official
-- documents (insurance certificates, trade licences, certifications) for
-- AI-assisted verification.
--
-- Safe to apply to production: all statements use IF NOT EXISTS guards.
-- ---------------------------------------------------------------------------

-- ── 1. contractor_documents ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS contractor_documents (
    id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    contractor_id        UUID         NOT NULL REFERENCES contractors(id) ON DELETE CASCADE,
    document_type        TEXT         NOT NULL
                                      CHECK (document_type IN ('insurance', 'licence', 'certification', 'other')),
    file_name            TEXT         NOT NULL,
    file_source          TEXT         NOT NULL,       -- base64 data URI or HTTPS URL
    status               TEXT         NOT NULL DEFAULT 'pending'
                                      CHECK (status IN ('pending', 'verified', 'needs_review', 'rejected')),
    extracted_data       JSONB,                       -- AI-extracted fields (insured name, policy no., expiry, etc.)
    verification_notes   TEXT,                        -- reason for needs_review / rejected
    expires_at           TIMESTAMPTZ,                 -- parsed from AI-extracted expiry date
    uploaded_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    verified_at          TIMESTAMPTZ,
    deleted_at           TIMESTAMPTZ,
    deleted_by_user_id   UUID
);

COMMENT ON TABLE contractor_documents IS
    'Official documents uploaded by contractors for AI-assisted field extraction and verification.';

COMMENT ON COLUMN contractor_documents.file_source IS
    'Base64 data URI or HTTPS URL pointing to the document image. Not exposed on public endpoints.';

COMMENT ON COLUMN contractor_documents.extracted_data IS
    'JSON object of AI-extracted fields. Schema varies by document_type — see document_verifier service.';

COMMENT ON COLUMN contractor_documents.expires_at IS
    'Expiry timestamp parsed from the document by the AI. NULL means no expiry or extraction failed.';

-- ── 2. Indexes ───────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS contractor_documents_contractor_idx
    ON contractor_documents (contractor_id)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS contractor_documents_status_idx
    ON contractor_documents (status)
    WHERE deleted_at IS NULL;

-- ── 3. Row Level Security ────────────────────────────────────────────────────

ALTER TABLE contractor_documents ENABLE ROW LEVEL SECURITY;

-- Contractors can read their own documents (all statuses, including needs_review).
DROP POLICY IF EXISTS contractor_documents_own_select ON contractor_documents;
CREATE POLICY contractor_documents_own_select ON contractor_documents
    FOR SELECT
    USING (
        contractor_id IN (
            SELECT id FROM contractors WHERE user_id = auth.uid()
        )
        AND deleted_at IS NULL
    );

-- Anyone can read verified, non-expired documents (for contractor profile pages).
-- file_source is excluded at the application layer (router selects specific columns).
DROP POLICY IF EXISTS contractor_documents_public_select ON contractor_documents;
CREATE POLICY contractor_documents_public_select ON contractor_documents
    FOR SELECT
    USING (
        status = 'verified'
        AND (expires_at IS NULL OR expires_at > now())
        AND deleted_at IS NULL
    );

-- Contractors can insert their own documents.
DROP POLICY IF EXISTS contractor_documents_insert ON contractor_documents;
CREATE POLICY contractor_documents_insert ON contractor_documents
    FOR INSERT
    WITH CHECK (
        contractor_id IN (
            SELECT id FROM contractors WHERE user_id = auth.uid()
        )
    );

-- Contractors can soft-delete (update deleted_at) their own documents.
DROP POLICY IF EXISTS contractor_documents_soft_delete ON contractor_documents;
CREATE POLICY contractor_documents_soft_delete ON contractor_documents
    FOR UPDATE
    USING (
        contractor_id IN (
            SELECT id FROM contractors WHERE user_id = auth.uid()
        )
    )
    WITH CHECK (
        contractor_id IN (
            SELECT id FROM contractors WHERE user_id = auth.uid()
        )
    );
