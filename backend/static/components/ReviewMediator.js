/**
 * ReviewMediator
 *
 * Self-contained vanilla-JS component that handles the full review
 * submission flow for both parties on a completed marketplace job.
 *
 * ── Escrow gate ──────────────────────────────────────────────────
 * The component fetches the job record on mount and only renders the
 * review form when  escrow_status === 'funds_released'.  Any other
 * state renders a locked placeholder explaining why the form is
 * unavailable.
 *
 * ── Categorical ratings ──────────────────────────────────────────
 * Instead of a single star rating the form captures three dimensions:
 *   • Cleanliness    (1–5)
 *   • Communication  (1–5)
 *   • Accuracy       (1–5)
 * The overall rating stored in the DB is the generated average.
 *
 * ── Double-blind ────────────────────────────────────────────────
 * After submission the component polls the visible_reviews view to
 * detect when both sides have reviewed, then reveals the peer review.
 *
 * ── AI summary ──────────────────────────────────────────────────
 * After a successful INSERT the component calls the review-sentiment
 * Edge Function, which asks Claude to extract Pros/Cons from the body
 * and writes them back to the review row.  The component then displays
 * the result.
 *
 * ── Usage ───────────────────────────────────────────────────────
 *
 *   <div id="review-mount"></div>
 *   <script src="/components/ReviewMediator.js"></script>
 *   <script>
 *     const rm = new ReviewMediator({
 *       container:          document.getElementById("review-mount"),
 *       supabaseUrl:        "https://xxxx.supabase.co",
 *       accessToken:        session.access_token,   // JWT from auth
 *       edgeFunctionBase:   "https://xxxx.supabase.co/functions/v1",
 *       jobId:              "job-uuid",
 *       reviewerId:         "auth-user-uuid",        // current user
 *       revieweeId:         "other-party-uuid",
 *       reviewerRole:       "client",                // or "contractor"
 *       revieweeRole:       "contractor",            // or "client"
 *       revieweeName:       "Dave's Plumbing",       // display name
 *     });
 *     rm.mount();
 *   </script>
 */

class ReviewMediator {
  // ── Constructor ───────────────────────────────────────────────────────────

  constructor(opts = {}) {
    this._cfg = {
      container:        opts.container        ?? null,
      supabaseUrl:      opts.supabaseUrl       ?? "",
      accessToken:      opts.accessToken       ?? "",
      edgeFunctionBase: opts.edgeFunctionBase  ?? "",
      jobId:            opts.jobId             ?? "",
      reviewerId:       opts.reviewerId        ?? "",
      revieweeId:       opts.revieweeId        ?? "",
      reviewerRole:     opts.reviewerRole      ?? "client",     // "client" | "contractor"
      revieweeRole:     opts.revieweeRole      ?? "contractor",
      revieweeName:     opts.revieweeName      ?? "the other party",
      pollInterval:     opts.pollInterval      ?? 15_000,       // ms between peer-reveal polls
    };

    this._pollTimer   = null;
    this._submittedId = null;   // review UUID once submitted
  }

  // ── Public API ───────────────────────────────────────────────────────────

  mount() {
    if (!this._cfg.container) {
      console.error("ReviewMediator: no container element provided");
      return;
    }
    this._injectStyles();
    this._render("loading");
    this._init();
  }

  unmount() {
    clearInterval(this._pollTimer);
    if (this._cfg.container) this._cfg.container.innerHTML = "";
  }

  // ── Init (escrow gate) ───────────────────────────────────────────────────

  async _init() {
    const { data: job, error } = await this._query(
      `${this._cfg.supabaseUrl}/rest/v1/jobs` +
      `?id=eq.${this._cfg.jobId}&select=id,status,escrow_status`,
    );

    if (error || !job || job.length === 0) {
      this._render("error", { message: "Could not load job details." });
      return;
    }

    const { escrow_status, status } = job[0];

    if (escrow_status !== "funds_released") {
      this._render("locked", { escrow_status });
      return;
    }

    // Check if the current user has already reviewed this job
    const { data: existing } = await this._query(
      `${this._cfg.supabaseUrl}/rest/v1/reviews` +
      `?job_id=eq.${this._cfg.jobId}&reviewer_id=eq.${this._cfg.reviewerId}&select=id`,
    );

    if (existing && existing.length > 0) {
      this._submittedId = existing[0].id;
      this._render("submitted");
      this._startPeerPoll();
      return;
    }

    // Ready to review
    this._render("form");
  }

  // ── Form submission ──────────────────────────────────────────────────────

  async _submit(formData) {
    this._render("submitting");

    const payload = {
      job_id:             this._cfg.jobId,
      reviewer_id:        this._cfg.reviewerId,
      reviewee_id:        this._cfg.revieweeId,
      reviewer_role:      this._cfg.reviewerRole,
      reviewee_role:      this._cfg.revieweeRole,
      rating_cleanliness:   parseInt(formData.cleanliness,   10),
      rating_communication: parseInt(formData.communication, 10),
      rating_accuracy:      parseInt(formData.accuracy,      10),
      body:               formData.body.trim() || null,
    };

    // INSERT via PostgREST — returns the created row
    const resp = await fetch(
      `${this._cfg.supabaseUrl}/rest/v1/reviews`,
      {
        method:  "POST",
        headers: {
          ...this._authHeaders(),
          "Content-Type":  "application/json",
          "Prefer":        "return=representation",
        },
        body: JSON.stringify(payload),
      },
    );

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ message: resp.statusText }));
      this._render("form", { submitError: err.message ?? "Submission failed. Please try again." });
      return;
    }

    const rows = await resp.json();
    this._submittedId = rows[0]?.id;

    // Fire-and-forget: call the sentiment Edge Function
    if (this._submittedId && formData.body.trim().length >= 10) {
      this._runSentimentAnalysis(this._submittedId);
    }

    this._render("submitted");
    this._startPeerPoll();
  }

  // ── AI sentiment (fire-and-forget) ──────────────────────────────────────

  async _runSentimentAnalysis(reviewId) {
    try {
      const resp = await fetch(
        `${this._cfg.edgeFunctionBase}/review-sentiment`,
        {
          method:  "POST",
          headers: { ...this._authHeaders(), "Content-Type": "application/json" },
          body:    JSON.stringify({ review_id: reviewId }),
        },
      );
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.ai_pros_cons) {
        this._showAiSummary(data.ai_pros_cons);
      }
    } catch {
      // Non-critical — silently swallow
    }
  }

  // ── Peer-reveal polling ──────────────────────────────────────────────────

  _startPeerPoll() {
    if (this._pollTimer) return;
    this._pollTimer = setInterval(() => this._checkPeerReview(), this._cfg.pollInterval);
    // Check immediately
    this._checkPeerReview();
  }

  async _checkPeerReview() {
    // Query visible_reviews for our own review to see if content_visible flipped
    const { data } = await this._query(
      `${this._cfg.supabaseUrl}/rest/v1/visible_reviews` +
      `?id=eq.${this._submittedId}&select=is_revealed,ai_pros_cons`,
    );

    if (!data || data.length === 0) return;

    const row = data[0];

    if (row.is_revealed) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;

      // Fetch the peer's review to display it
      const { data: peerData } = await this._query(
        `${this._cfg.supabaseUrl}/rest/v1/visible_reviews` +
        `?job_id=eq.${this._cfg.jobId}` +
        `&reviewer_id=neq.${this._cfg.reviewerId}` +
        `&select=rating,rating_cleanliness,rating_communication,rating_accuracy,body,ai_pros_cons`,
      );

      this._render("revealed", { peerReview: peerData?.[0] ?? null });
    }
  }

  // ── Renderers ────────────────────────────────────────────────────────────

  _render(state, ctx = {}) {
    const el = this._cfg.container;
    switch (state) {
      case "loading":
        el.innerHTML = `<div class="rm-wrap rm-loading">
          <div class="rm-spinner"></div>
          <p>Loading review status…</p>
        </div>`;
        break;

      case "locked":
        el.innerHTML = `<div class="rm-wrap rm-locked">
          <div class="rm-lock-icon">&#128274;</div>
          <h3>Review unavailable</h3>
          <p>Reviews open once payment has been released from escrow.</p>
          <p class="rm-status-pill">Escrow: <strong>${this._esc(ctx.escrow_status ?? "pending")}</strong></p>
        </div>`;
        break;

      case "error":
        el.innerHTML = `<div class="rm-wrap rm-error">
          <p><strong>Error:</strong> ${this._esc(ctx.message ?? "Something went wrong.")}</p>
        </div>`;
        break;

      case "form":
        el.innerHTML = `<div class="rm-wrap">
          <h3 class="rm-title">Review ${this._esc(this._cfg.revieweeName)}</h3>
          <p class="rm-subtitle">Your review is private until ${this._esc(this._cfg.revieweeName)} submits theirs (or 14 days pass).</p>

          ${ctx.submitError ? `<div class="rm-error-banner">${this._esc(ctx.submitError)}</div>` : ""}

          <form id="rm-form" novalidate>

            <fieldset class="rm-ratings">
              <legend>Rate each area (1 = poor &nbsp;·&nbsp; 5 = excellent)</legend>

              ${this._starRow("cleanliness",   "Cleanliness",   "How clean was the work area / property access?")}
              ${this._starRow("communication", "Communication", "How well did they communicate throughout?")}
              ${this._starRow("accuracy",      "Accuracy",      "How accurate was the quote vs. final cost, or the job description vs. actual work needed?")}
            </fieldset>

            <div class="rm-field">
              <label for="rm-body">Written review <span class="rm-opt">(optional)</span></label>
              <textarea id="rm-body" name="body" rows="4"
                placeholder="Describe your experience in your own words…"
                maxlength="2000"></textarea>
              <span class="rm-char-count" id="rm-char-count">0 / 2000</span>
            </div>

            <button type="submit" class="rm-submit-btn" id="rm-submit">
              Submit review
            </button>
          </form>
        </div>`;

        this._bindForm();
        break;

      case "submitting":
        el.innerHTML = `<div class="rm-wrap rm-loading">
          <div class="rm-spinner"></div>
          <p>Submitting your review…</p>
        </div>`;
        break;

      case "submitted": {
        const aiSection = el.querySelector(".rm-ai-section");  // preserve if already rendered
        el.innerHTML = `<div class="rm-wrap rm-submitted">
          <div class="rm-check-icon">&#10003;</div>
          <h3>Review submitted</h3>
          <p>We'll notify you once ${this._esc(this._cfg.revieweeName)} leaves their review.
             If they don't respond within 14 days, both reviews will be published automatically.</p>
          <div class="rm-ai-section" id="rm-ai-section">
            ${aiSection ? aiSection.innerHTML : '<p class="rm-ai-pending">Analysing your review…</p>'}
          </div>
        </div>`;
        break;
      }

      case "revealed":
        el.innerHTML = `<div class="rm-wrap rm-revealed">
          <h3>Both reviews are in!</h3>

          ${ctx.peerReview ? `
          <div class="rm-peer-review">
            <h4>${this._esc(this._cfg.revieweeName)}'s review of you</h4>
            ${this._ratingGrid(ctx.peerReview)}
            ${ctx.peerReview.body
              ? `<blockquote class="rm-review-body">${this._esc(ctx.peerReview.body)}</blockquote>`
              : ""}
            ${ctx.peerReview.ai_pros_cons ? this._prosConsHtml(ctx.peerReview.ai_pros_cons) : ""}
          </div>` : ""}

          <div class="rm-ai-section" id="rm-ai-section">
            <p class="rm-ai-pending">Loading AI summary…</p>
          </div>
        </div>`;

        // Reload our own AI summary now that the review is revealed
        if (this._submittedId) this._loadOwnAiSummary();
        break;
    }
  }

  // ── AI summary helpers ───────────────────────────────────────────────────

  _showAiSummary(aiProsCons) {
    const section = this._cfg.container.querySelector("#rm-ai-section");
    if (!section) return;
    section.innerHTML = `
      <div class="rm-ai-box">
        <h4 class="rm-ai-title">AI summary of your review</h4>
        ${this._prosConsHtml(aiProsCons)}
      </div>`;
  }

  async _loadOwnAiSummary() {
    if (!this._submittedId) return;
    const { data } = await this._query(
      `${this._cfg.supabaseUrl}/rest/v1/visible_reviews` +
      `?id=eq.${this._submittedId}&select=ai_pros_cons`,
    );
    const aiProsCons = data?.[0]?.ai_pros_cons;
    if (aiProsCons) this._showAiSummary(aiProsCons);
  }

  // ── HTML helpers ─────────────────────────────────────────────────────────

  _starRow(name, label, hint) {
    const stars = [1, 2, 3, 4, 5].map(
      (n) =>
        `<label class="rm-star" title="${n} star${n > 1 ? "s" : ""}">
          <input type="radio" name="${name}" value="${n}" required>
          <span>&#9733;</span>
        </label>`,
    ).join("");

    return `<div class="rm-star-row">
      <div class="rm-star-label">
        <span class="rm-star-name">${label}</span>
        <span class="rm-star-hint">${hint}</span>
      </div>
      <div class="rm-stars" data-field="${name}">${stars}</div>
    </div>`;
  }

  _ratingGrid(review) {
    const avg = ((review.rating_cleanliness + review.rating_communication + review.rating_accuracy) / 3).toFixed(1);
    return `<div class="rm-rating-grid">
      ${this._ratingPill("Cleanliness",   review.rating_cleanliness)}
      ${this._ratingPill("Communication", review.rating_communication)}
      ${this._ratingPill("Accuracy",      review.rating_accuracy)}
      <div class="rm-rating-avg">Overall: <strong>${avg} / 5</strong></div>
    </div>`;
  }

  _ratingPill(label, value) {
    const filled = Math.round(value);
    const stars  = "★".repeat(filled) + "☆".repeat(5 - filled);
    return `<div class="rm-pill">
      <span class="rm-pill-label">${label}</span>
      <span class="rm-pill-stars">${stars}</span>
    </div>`;
  }

  _prosConsHtml(aiProsCons) {
    const pros = (aiProsCons.pros ?? []).map((p) => `<li>${this._esc(p)}</li>`).join("");
    const cons = (aiProsCons.cons ?? []).map((c) => `<li>${this._esc(c)}</li>`).join("");
    const summary = aiProsCons.one_line_summary ?? "";

    return `<div class="rm-pros-cons">
      ${summary ? `<p class="rm-summary">${this._esc(summary)}</p>` : ""}
      ${pros ? `<div class="rm-pros"><strong>Pros</strong><ul>${pros}</ul></div>` : ""}
      ${cons ? `<div class="rm-cons"><strong>Cons</strong><ul>${cons}</ul></div>` : ""}
    </div>`;
  }

  // ── Form binding ─────────────────────────────────────────────────────────

  _bindForm() {
    const form    = this._cfg.container.querySelector("#rm-form");
    const bodyEl  = this._cfg.container.querySelector("#rm-body");
    const counter = this._cfg.container.querySelector("#rm-char-count");

    bodyEl?.addEventListener("input", () => {
      counter.textContent = `${bodyEl.value.length} / 2000`;
    });

    // Interactive star highlighting
    this._cfg.container.querySelectorAll(".rm-stars").forEach((group) => {
      group.addEventListener("change", () => this._highlightStars(group));
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();

      const fd = new FormData(form);
      const cleanliness   = fd.get("cleanliness");
      const communication = fd.get("communication");
      const accuracy      = fd.get("accuracy");

      if (!cleanliness || !communication || !accuracy) {
        // Briefly shake the rating section for feedback
        form.querySelector(".rm-ratings")?.classList.add("rm-shake");
        setTimeout(() => form.querySelector(".rm-ratings")?.classList.remove("rm-shake"), 600);
        return;
      }

      await this._submit({
        cleanliness,
        communication,
        accuracy,
        body: fd.get("body") ?? "",
      });
    });
  }

  _highlightStars(group) {
    const checked = group.querySelector("input:checked");
    if (!checked) return;
    const val = parseInt(checked.value, 10);
    group.querySelectorAll(".rm-star span").forEach((star, i) => {
      star.classList.toggle("rm-star-filled", i < val);
    });
  }

  // ── Fetch helpers ────────────────────────────────────────────────────────

  async _query(url) {
    try {
      const resp = await fetch(url, { headers: this._authHeaders() });
      if (!resp.ok) {
        const text = await resp.text();
        return { data: null, error: { message: text } };
      }
      const data = await resp.json();
      return { data, error: null };
    } catch (e) {
      return { data: null, error: { message: String(e) } };
    }
  }

  _authHeaders() {
    return {
      "apikey":        this._cfg.accessToken,
      "Authorization": `Bearer ${this._cfg.accessToken}`,
    };
  }

  _esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── Styles (injected once) ────────────────────────────────────────────────

  _injectStyles() {
    if (document.getElementById("rm-styles")) return;
    const style = document.createElement("style");
    style.id = "rm-styles";
    style.textContent = `
      .rm-wrap {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        max-width: 540px;
        margin: 0 auto;
        padding: 1.5rem;
        background: #fff;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        color: #111;
      }
      .rm-title  { font-size: 1.15rem; font-weight: 700; margin-bottom: 0.25rem; }
      .rm-subtitle { font-size: 0.82rem; color: #6b7280; margin-bottom: 1.25rem; }

      /* Loading */
      .rm-loading { display: flex; flex-direction: column; align-items: center; gap: 0.75rem; padding: 2rem; }
      .rm-spinner {
        width: 32px; height: 32px; border: 3px solid #e5e7eb;
        border-top-color: #4a6cf7; border-radius: 50%;
        animation: rm-spin 0.7s linear infinite;
      }
      @keyframes rm-spin { to { transform: rotate(360deg); } }

      /* Locked */
      .rm-locked { text-align: center; padding: 2rem 1rem; }
      .rm-lock-icon { font-size: 2.5rem; margin-bottom: 0.5rem; }
      .rm-locked h3 { margin-bottom: 0.5rem; }
      .rm-locked p  { color: #6b7280; font-size: 0.875rem; margin-bottom: 0.5rem; }
      .rm-status-pill {
        display: inline-block; margin-top: 0.75rem;
        padding: 0.25rem 0.75rem; background: #f3f4f6;
        border-radius: 99px; font-size: 0.8rem; color: #374151;
      }

      /* Error */
      .rm-error { color: #b91c1c; }
      .rm-error-banner {
        background: #fef2f2; border: 1px solid #fecaca;
        border-radius: 6px; padding: 0.6rem 0.75rem;
        font-size: 0.875rem; color: #b91c1c; margin-bottom: 1rem;
      }

      /* Star ratings */
      .rm-ratings {
        border: 1px solid #e5e7eb; border-radius: 8px;
        padding: 1rem; margin-bottom: 1.25rem;
        transition: box-shadow 0.2s;
      }
      .rm-ratings legend {
        font-size: 0.8rem; color: #6b7280;
        padding: 0 0.25rem; font-weight: 500;
      }
      .rm-star-row {
        display: flex; align-items: center; justify-content: space-between;
        padding: 0.6rem 0; border-bottom: 1px solid #f3f4f6;
      }
      .rm-star-row:last-child { border-bottom: none; }
      .rm-star-label { flex: 1; }
      .rm-star-name  { display: block; font-weight: 600; font-size: 0.9rem; }
      .rm-star-hint  { display: block; font-size: 0.75rem; color: #9ca3af; margin-top: 0.1rem; }
      .rm-stars      { display: flex; flex-direction: row-reverse; gap: 0.15rem; }
      .rm-star input { display: none; }
      .rm-star span  { font-size: 1.5rem; color: #d1d5db; cursor: pointer; transition: color 0.1s; }
      .rm-star:hover span,
      .rm-star:hover ~ .rm-star span,
      .rm-star span.rm-star-filled { color: #f59e0b; }

      /* Shake animation for missing ratings */
      @keyframes rm-shake {
        0%,100% { transform: translateX(0); }
        20%,60% { transform: translateX(-6px); }
        40%,80% { transform: translateX(6px); }
      }
      .rm-shake { animation: rm-shake 0.5s ease; box-shadow: 0 0 0 2px #f87171; }

      /* Text field */
      .rm-field { margin-bottom: 1.25rem; position: relative; }
      .rm-field label {
        display: block; font-size: 0.8rem; font-weight: 600;
        color: #374151; margin-bottom: 0.4rem;
      }
      .rm-opt { font-weight: 400; color: #9ca3af; }
      .rm-field textarea {
        width: 100%; padding: 0.65rem 0.75rem;
        border: 1px solid #d1d5db; border-radius: 6px;
        font-size: 0.9rem; font-family: inherit; resize: vertical;
        outline: none; transition: border-color 0.15s;
      }
      .rm-field textarea:focus { border-color: #4a6cf7; }
      .rm-char-count {
        position: absolute; bottom: 0.4rem; right: 0.6rem;
        font-size: 0.72rem; color: #9ca3af; pointer-events: none;
      }

      /* Submit button */
      .rm-submit-btn {
        width: 100%; padding: 0.75rem;
        background: #4a6cf7; color: #fff;
        border: none; border-radius: 8px;
        font-size: 0.95rem; font-weight: 600;
        cursor: pointer; transition: background 0.15s;
      }
      .rm-submit-btn:hover { background: #3b5ce5; }
      .rm-submit-btn:disabled { background: #a5b4fc; cursor: not-allowed; }

      /* Submitted state */
      .rm-submitted { text-align: center; }
      .rm-check-icon {
        display: inline-flex; align-items: center; justify-content: center;
        width: 52px; height: 52px; background: #d1fae5;
        border-radius: 50%; font-size: 1.6rem; color: #059669;
        margin-bottom: 0.75rem;
      }
      .rm-submitted h3 { margin-bottom: 0.5rem; }
      .rm-submitted p  { font-size: 0.875rem; color: #6b7280; margin-bottom: 0.5rem; }

      /* Revealed state */
      .rm-revealed h3 { margin-bottom: 1rem; }
      .rm-peer-review {
        background: #f9fafb; border-radius: 8px;
        padding: 1rem; margin-bottom: 1rem;
      }
      .rm-peer-review h4 { font-size: 0.9rem; font-weight: 700; margin-bottom: 0.75rem; }
      .rm-review-body {
        font-style: italic; color: #374151; font-size: 0.875rem;
        border-left: 3px solid #d1d5db; margin: 0.75rem 0; padding-left: 0.75rem;
      }

      /* Rating grid */
      .rm-rating-grid { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.75rem; }
      .rm-pill {
        display: flex; align-items: center; gap: 0.4rem;
        background: #fff; border: 1px solid #e5e7eb;
        border-radius: 6px; padding: 0.3rem 0.6rem; font-size: 0.8rem;
      }
      .rm-pill-label { color: #6b7280; }
      .rm-pill-stars { color: #f59e0b; letter-spacing: 1px; }
      .rm-rating-avg { font-size: 0.8rem; color: #374151; margin-top: 0.25rem; width: 100%; }

      /* Pros / cons */
      .rm-ai-box {
        background: #f0f9ff; border: 1px solid #bae6fd;
        border-radius: 8px; padding: 0.85rem 1rem; margin-top: 1rem;
        text-align: left;
      }
      .rm-ai-title  { font-size: 0.8rem; font-weight: 700; color: #0369a1; margin-bottom: 0.5rem; }
      .rm-ai-pending { font-size: 0.8rem; color: #9ca3af; margin: 0; }
      .rm-summary { font-size: 0.85rem; color: #374151; margin-bottom: 0.6rem; }
      .rm-pros-cons { font-size: 0.85rem; }
      .rm-pros ul, .rm-cons ul { list-style: none; padding: 0; margin: 0.3rem 0 0.6rem; }
      .rm-pros li::before { content: "✓ "; color: #059669; font-weight: 700; }
      .rm-cons li::before { content: "✗ "; color: #dc2626; font-weight: 700; }
      .rm-pros strong { color: #059669; }
      .rm-cons strong { color: #dc2626; }
    `;
    document.head.appendChild(style);
  }
}

// Export for module environments; no-op in plain <script> usage
if (typeof module !== "undefined") module.exports = ReviewMediator;
