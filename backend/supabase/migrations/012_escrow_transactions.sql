-- Migration 012: Escrow payment transactions
--
-- Adds:
--   escrow_transactions — full payment lifecycle per job
--   contractor_details.stripe_account_id — Stripe Connect payout account
--
-- The jobs.escrow_status column (pending | held | funds_released | refunded)
-- already exists (migration 006) and remains the fast-read status on the job.
-- escrow_transactions holds the detailed audit trail and provider references.

-- Stripe Connect account for contractor payouts (set during onboarding)
ALTER TABLE contractor_details
  ADD COLUMN IF NOT EXISTS stripe_account_id TEXT;

-- One escrow transaction per job.
-- UNIQUE(job_id) enforces the single-escrow-per-job rule at the DB level.
CREATE TABLE IF NOT EXISTS escrow_transactions (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id                UUID NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
  bid_id                UUID REFERENCES bids(id),
  homeowner_id          UUID NOT NULL REFERENCES auth.users(id),
  contractor_id         UUID NOT NULL,            -- contractors.id (not auth.users)
  amount_pence          INTEGER NOT NULL CHECK (amount_pence > 0),
  currency              TEXT NOT NULL DEFAULT 'gbp',

  -- Payment provider fields (provider-agnostic naming)
  provider              TEXT NOT NULL DEFAULT 'stripe',
  provider_ref          TEXT,                     -- e.g. Stripe PaymentIntent ID
  provider_transfer_ref TEXT,                     -- Stripe Transfer ID on release
  provider_refund_ref   TEXT,                     -- Stripe Refund ID on refund

  status                TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                          'pending',      -- intent created, awaiting customer payment
                          'processing',   -- customer submitted payment, awaiting confirmation
                          'held',         -- funds confirmed and held on platform
                          'released',     -- funds transferred to contractor
                          'refunded',     -- funds returned to homeowner
                          'failed'        -- payment failed
                        )),

  initiated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  held_at               TIMESTAMPTZ,
  released_at           TIMESTAMPTZ,
  refunded_at           TIMESTAMPTZ,

  release_note          TEXT,          -- homeowner's approval note
  failure_reason        TEXT,          -- provider error message on failure
  metadata              JSONB,         -- provider-specific raw event data

  UNIQUE (job_id)
);

-- RLS: homeowners and contractors can read their own transactions; only
-- service-role (backend) writes.
ALTER TABLE escrow_transactions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "homeowner_read_own_escrow" ON escrow_transactions
  FOR SELECT USING (homeowner_id = auth.uid());

CREATE POLICY "contractor_read_own_escrow" ON escrow_transactions
  FOR SELECT USING (contractor_id::TEXT = auth.uid()::TEXT);

-- Index for webhook lookups by provider reference (payment intent ID)
CREATE INDEX IF NOT EXISTS escrow_provider_ref_idx
  ON escrow_transactions (provider_ref)
  WHERE provider_ref IS NOT NULL;
