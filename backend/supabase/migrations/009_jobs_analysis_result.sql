-- ============================================================
-- Migration 009: Add analysis_result to jobs
--
-- Problem
-- -------
-- The Gemini analysis result (description, problem_type, urgency,
-- materials_involved, clarifying_questions, video_metadata, …) is
-- returned to the client on POST /analyse but has no home on the
-- jobs table.  The Lovable PWA's project-detail sheet renders these
-- fields, but the data is only available in React state after the
-- initial API call — a page refresh loses everything.
--
-- Fix
-- ---
-- Add an analysis_result JSONB column to jobs.  When the homeowner
-- creates a job from an analysis, the Lovable frontend includes the
-- full Gemini JSON in the INSERT payload.  The data then:
--   • Lives with the job for the full job lifecycle
--   • Is readable by the homeowner (jobs: owner full access)
--   • Is readable by contractors on open jobs
--     (jobs: contractors can read open) — intentional, so contractors
--     can make informed bids based on the AI assessment
--   • Is readable by Lovable's project-detail sheet at any time
--     via supabase.from('jobs').select('*').eq('id', jobId)
--
-- The column is nullable so existing jobs and jobs created without
-- an analysis (e.g. manually typed) are unaffected.
-- ============================================================

ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS analysis_result JSONB;

COMMENT ON COLUMN public.jobs.analysis_result IS
    'Gemini AI assessment (from POST /analyse or POST /analyse/photos). '
    'Populated by the client when creating a job from an analysis. '
    'Readable by contractors on open jobs via the existing RLS policy.';
