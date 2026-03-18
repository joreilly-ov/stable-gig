/**
 * Supabase Edge Function: review-sentiment
 *
 * Reads a submitted review, calls the Claude API to extract a structured
 * Pros / Cons list from the free-text body, then:
 *   1. Writes the result to reviews.ai_pros_cons
 *   2. Refreshes contractor_details.ai_review_summary with an aggregated
 *      summary built from all of the contractor's revealed reviews.
 *
 * Required secrets (Supabase dashboard → Project Settings → Edge Functions):
 *   ANTHROPIC_API_KEY
 *   SUPABASE_URL
 *   SUPABASE_SERVICE_KEY   ← service role; bypasses RLS for internal writes
 *
 * POST /review-sentiment
 * Body: { "review_id": "<uuid>" }
 *
 * The caller (ReviewMediator component) hits this immediately after a
 * successful review INSERT.  The function is idempotent — re-calling it
 * for the same review_id simply overwrites ai_pros_cons with a fresh result.
 */

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// ── Config ────────────────────────────────────────────────────────────────────

const ANTHROPIC_API_KEY  = Deno.env.get("ANTHROPIC_API_KEY")  ?? "";
const SUPABASE_URL        = Deno.env.get("SUPABASE_URL")        ?? "";
const SUPABASE_SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_KEY") ?? "";

// Use Haiku for fast, cheap sentiment extraction
const CLAUDE_MODEL = "claude-haiku-4-5-20251001";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface ReviewRow {
  id: string;
  body: string | null;
  reviewer_role: "client" | "contractor";
  reviewee_id: string;
  rating_cleanliness: number;
  rating_communication: number;
  rating_accuracy: number;
  rating: number;
}

interface AiProsCons {
  pros: string[];
  cons: string[];
  one_line_summary: string;
}

// ── Main handler ──────────────────────────────────────────────────────────────

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS_HEADERS });
  }
  if (req.method !== "POST") {
    return json({ error: "Method not allowed" }, 405);
  }

  if (!ANTHROPIC_API_KEY) {
    return json(
      { error: "ANTHROPIC_API_KEY is not configured. Add it in Supabase → Project Settings → Edge Functions → Secrets." },
      500,
    );
  }

  let review_id: string;
  try {
    ({ review_id } = await req.json());
    if (!review_id) throw new Error("review_id is required");
  } catch (e) {
    return json({ error: String(e) }, 400);
  }

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);

  // ── Step 1: Fetch the review ───────────────────────────────────────────────
  const { data: review, error: fetchErr } = await supabase
    .from("reviews")
    .select("id, body, reviewer_role, reviewee_id, rating_cleanliness, rating_communication, rating_accuracy, rating")
    .eq("id", review_id)
    .single<ReviewRow>();

  if (fetchErr || !review) {
    return json({ error: "Review not found", detail: fetchErr?.message }, 404);
  }

  if (!review.body || review.body.trim().length < 10) {
    // Body is optional — skip AI analysis silently and return empty result
    return json({ skipped: true, reason: "Review body too short or absent" }, 200);
  }

  // ── Step 2: Call Claude for Pros / Cons ───────────────────────────────────
  const reviewerLabel = review.reviewer_role === "client" ? "homeowner" : "tradesperson";
  const revieweeLabel = review.reviewer_role === "client" ? "tradesperson" : "homeowner";

  const prompt = `You are a review analyst for a home-repair marketplace. A ${reviewerLabel} left the following review about a ${revieweeLabel}.

Review text:
"""
${review.body}
"""

Ratings given:
- Overall (avg): ${review.rating}/5
- Cleanliness:   ${review.rating_cleanliness}/5
- Communication: ${review.rating_communication}/5
- Accuracy:      ${review.rating_accuracy}/5

Extract a structured Pros and Cons list from this review.

Rules:
- Maximum 3 pros and 3 cons.
- Only include points clearly supported by the review text — do not invent or embellish.
- Each point should be a short, factual phrase (max 10 words).
- one_line_summary should be a neutral, 10–20 word sentence capturing the overall sentiment.
- If there are genuinely no pros or no cons, return an empty array.

Respond with ONLY a JSON object — no markdown, no explanation:
{
  "pros": ["...", "..."],
  "cons": ["...", "..."],
  "one_line_summary": "..."
}`;

  let aiResult: AiProsCons;
  try {
    const anthropicResp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: CLAUDE_MODEL,
        max_tokens: 512,
        messages: [{ role: "user", content: prompt }],
      }),
    });

    if (!anthropicResp.ok) {
      const detail = await anthropicResp.text();
      throw new Error(`Anthropic API error (${anthropicResp.status}): ${detail}`);
    }

    const anthropicData = await anthropicResp.json();
    let raw: string = (anthropicData.content?.[0]?.text ?? "").trim();

    // Strip any accidental markdown fences
    if (raw.startsWith("```")) {
      raw = raw.replace(/^```(?:json)?\s*/m, "").replace(/\s*```$/m, "").trim();
    }

    aiResult = JSON.parse(raw);

    if (!Array.isArray(aiResult.pros) || !Array.isArray(aiResult.cons)) {
      throw new Error("AI response missing pros/cons arrays");
    }

    // Clamp to max 3 each
    aiResult.pros = aiResult.pros.slice(0, 3);
    aiResult.cons = aiResult.cons.slice(0, 3);
  } catch (e) {
    return json({ error: "AI analysis failed", detail: String(e) }, 500);
  }

  // ── Step 3: Write ai_pros_cons back to the review ─────────────────────────
  const { error: updateReviewErr } = await supabase
    .from("reviews")
    .update({ ai_pros_cons: aiResult })
    .eq("id", review_id);

  if (updateReviewErr) {
    return json({ error: "Failed to save AI result", detail: updateReviewErr.message }, 500);
  }

  // ── Step 4: Refresh the contractor's aggregated profile summary ──────────
  // Only refresh if this review is about a contractor (client reviewing them).
  if (review.reviewer_role === "client") {
    await refreshContractorSummary(supabase, review.reviewee_id);
  }

  return json({ success: true, ai_pros_cons: aiResult }, 200);
});

// ── refreshContractorSummary ──────────────────────────────────────────────────
//
// Pulls all revealed ai_pros_cons for the contractor, then asks Claude to
// distill them into a profile-level "Top Pros / Top Cons" summary.
// Writes the result to contractor_details.ai_review_summary.

async function refreshContractorSummary(
  // deno-lint-ignore no-explicit-any
  supabase: any,
  contractorId: string,
): Promise<void> {
  // Fetch all revealed reviews for this contractor that have AI analysis
  const { data: revealedReviews } = await supabase
    .from("visible_reviews")          // double-blind enforced by the view
    .select("ai_pros_cons, rating")
    .eq("reviewee_id", contractorId)
    .eq("reviewee_role", "contractor")
    .not("ai_pros_cons", "is", null);

  if (!revealedReviews || revealedReviews.length === 0) return;

  // Collect all pros and cons across reviews
  const allPros: string[] = revealedReviews.flatMap(
    (r: { ai_pros_cons: AiProsCons }) => r.ai_pros_cons?.pros ?? [],
  );
  const allCons: string[] = revealedReviews.flatMap(
    (r: { ai_pros_cons: AiProsCons }) => r.ai_pros_cons?.cons ?? [],
  );

  if (allPros.length === 0 && allCons.length === 0) return;

  const aggregatePrompt = `You are summarising customer reviews for a tradesperson's marketplace profile.

All positive points mentioned across ${revealedReviews.length} review(s):
${allPros.map((p, i) => `${i + 1}. ${p}`).join("\n") || "None"}

All negative points mentioned across ${revealedReviews.length} review(s):
${allCons.map((c, i) => `${i + 1}. ${c}`).join("\n") || "None"}

Identify the most frequently mentioned themes and return the top 3 pros and top 3 cons.
Merge near-duplicate points into a single consolidated phrase.

Respond with ONLY a JSON object — no markdown, no explanation:
{
  "top_pros": ["...", "..."],
  "top_cons": ["...", "..."]
}`;

  try {
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": Deno.env.get("ANTHROPIC_API_KEY") ?? "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 256,
        messages: [{ role: "user", content: aggregatePrompt }],
      }),
    });

    if (!resp.ok) return;

    const data = await resp.json();
    let raw: string = (data.content?.[0]?.text ?? "").trim();
    if (raw.startsWith("```")) {
      raw = raw.replace(/^```(?:json)?\s*/m, "").replace(/\s*```$/m, "").trim();
    }

    const summary = JSON.parse(raw);
    summary.last_updated = new Date().toISOString();
    summary.review_count = revealedReviews.length;

    await supabase
      .from("contractor_details")
      .update({ ai_review_summary: summary })
      .eq("id", contractorId);
  } catch {
    // Profile summary refresh is best-effort; do not fail the main request
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function json(data: unknown, status: number): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
