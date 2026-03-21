-- ============================================================
-- Migration 008: Column-level security for reviews.private_feedback
--
-- Problem
-- -------
-- The RLS policies from migration 005 allow:
--   • reviewers  to SELECT their own row   (reviewer_id = auth.uid())
--   • reviewees  to SELECT revealed rows   (reviewee_id = auth.uid() AND revealed)
--
-- Both policies match the full row — including private_feedback — when a
-- client queries the raw `reviews` table via PostgREST.  The visible_reviews
-- view already omits private_feedback, but a caller using
--   supabase.from('reviews').select('*')
-- can bypass the view and read private_feedback directly.
--
-- Fix
-- ---
-- Strip table-level SELECT from the `authenticated` and `anon` roles and
-- re-grant SELECT column-by-column, deliberately excluding private_feedback.
-- RLS policies continue to restrict WHICH rows each role can see; this layer
-- restricts WHICH columns they can read regardless of row-level access.
--
-- The service role bypasses both RLS and column-level grants, so admin
-- tooling that reads the raw table with SUPABASE_SERVICE_KEY is unaffected.
-- ============================================================


-- ── 1. Revoke table-level SELECT ─────────────────────────────────────────────
--
-- Supabase's default setup grants SELECT on all public tables to both roles.
-- We must revoke that grant before column-level grants can take effect —
-- a table-level grant always overrides column-level revokes in PostgreSQL.

REVOKE SELECT ON public.reviews FROM authenticated, anon;


-- ── 2. Re-grant SELECT on every column EXCEPT private_feedback ───────────────
--
-- authenticated users can read their own/revealed review rows (still filtered
-- by the existing RLS policies) — but private_feedback is never in scope.

GRANT SELECT (
    id,
    job_id,
    reviewer_id,
    reviewee_id,
    reviewer_role,
    reviewee_role,
    rating_cleanliness,
    rating_communication,
    rating_quality,
    rating,
    body,
    ai_pros_cons,
    content_visible,
    reveal_at,
    submitted_at
) ON public.reviews TO authenticated;

-- anon has no SELECT policies on reviews anyway, so we grant nothing —
-- being explicit here prevents accidental future grants from reopening it.


-- ── 3. INSERT privilege is unchanged ────────────────────────────────────────
--
-- authenticated users still need INSERT to submit reviews.  The INSERT
-- payload may include private_feedback (column-level grants don't restrict
-- INSERT targets, only SELECT projections).

-- No change needed — INSERT was granted separately and is unaffected by
-- the SELECT revoke above.


-- ── 4. visible_reviews view is unaffected ────────────────────────────────────
--
-- Views run as their owner (typically postgres / service role) via Supabase's
-- default security model, so the view can still SELECT all underlying columns.
-- Since visible_reviews explicitly omits private_feedback from its column list,
-- it continues to be the safe public-facing query surface.
