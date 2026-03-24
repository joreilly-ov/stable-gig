-- Migration 011: RFP document storage + contractor profile embeddings
--
-- Adds:
--   jobs.rfp_document          JSONB  — structured RFP generated from Gemini analysis
--   jobs.cost_estimate_low_pence  INT — lower bound of AI cost estimate (GBP pence)
--   jobs.cost_estimate_high_pence INT — upper bound of AI cost estimate (GBP pence)
--   jobs.permit_required       BOOL  — AI flag for permit considerations
--   jobs.permit_notes          TEXT  — human-readable permit guidance
--   jobs.job_embedding         vector(768) — embedding of RFP text for matching
--
--   contractor_details.profile_embedding  vector(768) — embedding of contractor profile
--   contractor_details.profile_text       TEXT        — raw text that was embedded
--
-- Creates:
--   match_contractors() — RPC function for cosine-similarity contractor search

-- pgvector is pre-installed on Supabase; this is a no-op if already enabled.
CREATE EXTENSION IF NOT EXISTS vector;

-- RFP and cost-estimate fields on jobs ----------------------------------------
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS rfp_document           JSONB,
  ADD COLUMN IF NOT EXISTS cost_estimate_low_pence  INTEGER CHECK (cost_estimate_low_pence  > 0),
  ADD COLUMN IF NOT EXISTS cost_estimate_high_pence INTEGER CHECK (cost_estimate_high_pence > 0),
  ADD COLUMN IF NOT EXISTS permit_required        BOOLEAN,
  ADD COLUMN IF NOT EXISTS permit_notes           TEXT,
  ADD COLUMN IF NOT EXISTS job_embedding          vector(768);

-- Contractor profile embeddings -----------------------------------------------
ALTER TABLE contractor_details
  ADD COLUMN IF NOT EXISTS profile_embedding vector(768),
  ADD COLUMN IF NOT EXISTS profile_text      TEXT;

-- IVFFlat index — fast approximate cosine-similarity search over contractor profiles.
-- lists = 100 is appropriate for up to ~1 M rows; tune if the table grows significantly.
CREATE INDEX IF NOT EXISTS contractor_profile_embedding_idx
  ON contractor_details USING ivfflat (profile_embedding vector_cosine_ops)
  WITH (lists = 100);

-- match_contractors ------------------------------------------------------------
-- Returns contractors ordered by cosine similarity to a query embedding.
-- Falls back gracefully: if match_activity is NULL, all contractors are considered.
-- Called from the FastAPI layer via db.rpc("match_contractors", {...}).
CREATE OR REPLACE FUNCTION match_contractors(
  query_embedding vector(768),
  match_activity  text DEFAULT NULL,
  match_limit     int  DEFAULT 10
)
RETURNS TABLE (
  contractor_id uuid,
  similarity    float
)
LANGUAGE sql STABLE AS $$
  SELECT
    cd.id                                            AS contractor_id,
    1 - (cd.profile_embedding <=> query_embedding)  AS similarity
  FROM contractor_details cd
  JOIN contractors c ON c.id = cd.id
  WHERE cd.profile_embedding IS NOT NULL
    AND (match_activity IS NULL OR match_activity = ANY(c.activities))
  ORDER BY cd.profile_embedding <=> query_embedding
  LIMIT match_limit;
$$;
