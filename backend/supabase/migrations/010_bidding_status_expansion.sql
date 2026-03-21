-- ============================================================
-- Migration 010: Expand job status for bidding lifecycle
--
-- Adds 'draft' and 'in_progress' to the jobs.status CHECK
-- constraint to support the full MVP bidding flow:
--
--   draft       → homeowner created the job, not yet visible
--                 to contractors (editing / reviewing AI result)
--   open        → posted for bids; contractors can view and bid
--   awarded     → homeowner accepted a bid; work agreed
--   in_progress → contractor has started the work
--   completed   → job finished
--   cancelled   → job withdrawn
--
-- The bids table (from migration 003) already has the fields
-- needed for MVP bidding:
--   amount_pence  — quote price in pence (integer, avoids float)
--   note          — contractor's work description / scope of work
--   status        — pending | accepted | rejected
--
-- No schema changes to bids are required.
-- ============================================================

-- Drop the existing 4-value constraint and replace with 6-value version.
-- IF NOT EXISTS is not available for CONSTRAINT so we use a safe pattern.
ALTER TABLE public.jobs
    DROP CONSTRAINT IF EXISTS jobs_status_check;

ALTER TABLE public.jobs
    ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('draft', 'open', 'awarded', 'in_progress', 'completed', 'cancelled'));

-- Update the column comment to reflect the new lifecycle
COMMENT ON COLUMN public.jobs.status IS
    'draft → open → awarded → in_progress → completed | cancelled.';
