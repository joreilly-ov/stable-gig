-- Migration 021: add crypto_wallet_address to contractors
--
-- Stores a contractor's on-chain wallet address (e.g. an Ethereum-compatible
-- address on Base).  When set, the escrow release flow will pay out in USDC
-- via Circle instead of Stripe.  NULL means Stripe (or manual payout if
-- stripe_account_id is also absent).

ALTER TABLE contractors
    ADD COLUMN IF NOT EXISTS crypto_wallet_address TEXT;
