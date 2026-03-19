/**
 * ReviewMediator.js — Premium review form + listing component.
 *
 * The frontend calls the backend review API over HTTP.
 * No backend logic is duplicated here.
 *
 * Usage (form mode — submit a new review):
 *
 *   import ReviewMediator from './components/ReviewMediator.js';
 *
 *   const rm = new ReviewMediator({
 *     containerId:   'review-root',   // id of the mount element, or the element itself
 *     apiBase:       '',              // '' = same origin; or 'https://api.example.com'
 *     contractorId:  '<uuid>',
 *     jobId:         '<uuid>',        // required for form mode
 *     token:         '<supabase-jwt>',// required for form mode
 *     mode:          'form',          // 'form' | 'list' | 'both'
 *     onSuccess:     (review) => {},  // optional callback after successful submission
 *   });
 *   rm.mount();
 */

// ─── Constants ────────────────────────────────────────────────────────────────

const _CATEGORIES = [
  { key: 'quality',       label: 'Work Quality',   desc: 'Standard of workmanship delivered' },
  { key: 'timeliness',    label: 'Timeliness',      desc: 'Started and finished on schedule' },
  { key: 'communication', label: 'Communication',   desc: 'Kept you informed throughout' },
  { key: 'value',         label: 'Value for Money', desc: 'Fair price for the work done' },
  { key: 'tidiness',      label: 'Tidiness',        desc: 'Left the site clean and tidy' },
];

const _RATING_LABELS = ['', 'Poor', 'Fair', 'Good', 'Very good', 'Excellent'];

const _BAR_COLOR = (v) => ['', '#ef4444', '#f97316', '#eab308', '#3b82f6', '#22c55e'][v] || '#4a6cf7';

// ─── Styles ───────────────────────────────────────────────────────────────────

const _CSS = `
/* ── ReviewMediator: scoped to .rm-root ── */

.rm-root * { box-sizing: border-box; }

.rm-card {
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,.06), 0 4px 16px rgba(0,0,0,.08);
  padding: 2rem;
  max-width: 560px;
  width: 100%;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: #111;
}

/* ── Header ── */

.rm-header { margin-bottom: 1.5rem; }

.rm-title {
  font-size: 1.2rem;
  font-weight: 700;
  color: #111;
  margin: 0 0 0.2rem;
  line-height: 1.3;
}

.rm-subtitle {
  font-size: 0.85rem;
  color: #6b7280;
  margin: 0 0 0.75rem;
}

.rm-trust-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 999px;
  padding: 0.2rem 0.65rem;
  font-size: 0.72rem;
  font-weight: 600;
  color: #16a34a;
  letter-spacing: 0.02em;
}

/* ── Overall star rating ── */

.rm-overall {
  padding-bottom: 1.5rem;
  margin-bottom: 1.5rem;
  border-bottom: 1px solid #f3f4f6;
}

.rm-section-label {
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: #9ca3af;
  margin-bottom: 0.6rem;
}

.rm-stars {
  display: flex;
  gap: 0.3rem;
}

.rm-star {
  width: 34px;
  height: 34px;
  cursor: pointer;
  color: #e5e7eb;
  background: none;
  border: none;
  padding: 0;
  transition: color 0.12s, transform 0.1s;
}

.rm-star svg { display: block; width: 100%; height: 100%; }

.rm-star:hover,
.rm-star.rm-active { color: #f59e0b; }

.rm-star:active { transform: scale(0.88); }

.rm-star-hint {
  font-size: 0.8rem;
  color: #9ca3af;
  margin-top: 0.4rem;
  min-height: 1.2em;
  transition: color 0.15s;
}

/* ── Category rows ── */

.rm-categories {
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
  margin-bottom: 1.5rem;
}

.rm-cat-meta {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.35rem;
}

.rm-cat-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #374151;
}

.rm-cat-value {
  font-size: 0.78rem;
  font-weight: 500;
  color: #9ca3af;
  transition: color 0.2s;
  min-width: 5rem;
  text-align: right;
}

.rm-cat-value.rm-rated { color: #374151; }

/* Progress track */
.rm-track {
  height: 7px;
  background: #f3f4f6;
  border-radius: 999px;
  overflow: hidden;
  margin-bottom: 0.45rem;
  cursor: pointer;
}

.rm-fill {
  height: 100%;
  width: 0%;
  border-radius: 999px;
  background: #4a6cf7;
  transition: width 0.38s cubic-bezier(.34,1.56,.64,1), background-color 0.25s ease;
}

/* Dot buttons */
.rm-dots {
  display: flex;
  gap: 0.35rem;
}

.rm-dot {
  flex: 1;
  height: 28px;
  border: 1.5px solid #e5e7eb;
  border-radius: 7px;
  background: #fff;
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 600;
  color: #9ca3af;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: border-color 0.12s, background 0.12s, color 0.12s;
  user-select: none;
}

.rm-dot:hover {
  border-color: #4a6cf7;
  color: #4a6cf7;
}

.rm-dot.rm-active {
  background: #4a6cf7;
  border-color: #4a6cf7;
  color: #fff;
}

/* ── Comment ── */

.rm-comment-label {
  display: block;
  font-size: 0.875rem;
  font-weight: 600;
  color: #374151;
  margin-bottom: 0.4rem;
}

.rm-comment-opt {
  font-size: 0.75rem;
  font-weight: 400;
  color: #9ca3af;
  margin-left: 0.35rem;
}

.rm-textarea {
  width: 100%;
  border: 1.5px solid #e5e7eb;
  border-radius: 10px;
  padding: 0.7rem 0.9rem;
  font-size: 0.875rem;
  font-family: inherit;
  color: #111;
  resize: vertical;
  min-height: 90px;
  outline: none;
  transition: border-color 0.15s;
}

.rm-textarea:focus { border-color: #4a6cf7; }
.rm-textarea::placeholder { color: #d1d5db; }

/* ── Footer row ── */

.rm-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 1.25rem;
  gap: 1rem;
}

.rm-privacy {
  font-size: 0.72rem;
  color: #9ca3af;
  line-height: 1.4;
  flex: 1;
}

.rm-btn {
  padding: 0.625rem 1.4rem;
  border-radius: 10px;
  border: none;
  background: #4a6cf7;
  color: #fff;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  transition: background 0.15s, transform 0.1s, opacity 0.15s;
  flex-shrink: 0;
}

.rm-btn:hover { background: #3a5ce8; }
.rm-btn:active { transform: scale(0.97); }
.rm-btn:disabled { opacity: 0.55; cursor: not-allowed; transform: none; }

/* ── Alerts ── */

.rm-alert {
  padding: 0.7rem 0.9rem;
  border-radius: 8px;
  font-size: 0.85rem;
  line-height: 1.5;
  margin-top: 0.9rem;
}

.rm-alert.rm-error {
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

.rm-alert.rm-success {
  background: #f0fdf4;
  color: #15803d;
  border: 1px solid #bbf7d0;
}

/* ── Spinner ── */

@keyframes rm-spin { to { transform: rotate(360deg); } }

.rm-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255,255,255,.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: rm-spin 0.65s linear infinite;
  display: inline-block;
  flex-shrink: 0;
}

/* ── List / summary ── */

.rm-list-summary {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 1.25rem;
  align-items: center;
  padding-bottom: 1.5rem;
  margin-bottom: 1.5rem;
  border-bottom: 1px solid #f3f4f6;
}

.rm-big-score {
  font-size: 3.25rem;
  font-weight: 800;
  color: #111;
  line-height: 1;
  text-align: center;
}

.rm-big-sub {
  font-size: 0.75rem;
  color: #6b7280;
  text-align: center;
  margin-top: 0.2rem;
}

.rm-sum-bars { display: flex; flex-direction: column; gap: 0.5rem; }

.rm-sum-row {
  display: grid;
  grid-template-columns: 8.5rem 1fr 2.25rem;
  gap: 0.5rem;
  align-items: center;
  font-size: 0.78rem;
}

.rm-sum-label { color: #6b7280; }

.rm-sum-track {
  height: 6px;
  background: #f3f4f6;
  border-radius: 999px;
  overflow: hidden;
}

.rm-sum-fill {
  height: 100%;
  background: #4a6cf7;
  border-radius: 999px;
  width: 0%;
  transition: width 0.65s cubic-bezier(.34,1.56,.64,1);
}

.rm-sum-val {
  color: #374151;
  font-weight: 600;
  text-align: right;
}

/* ── Individual review cards ── */

.rm-review-list { display: flex; flex-direction: column; gap: 0.9rem; }

.rm-review-card {
  border: 1px solid #f3f4f6;
  border-radius: 12px;
  padding: 1rem 1.1rem;
  transition: box-shadow 0.15s;
}

.rm-review-card:hover {
  box-shadow: 0 2px 8px rgba(0,0,0,.06);
}

.rm-review-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 0.5rem;
}

.rm-reviewer { font-weight: 600; font-size: 0.875rem; color: #111; }

.rm-review-date { font-size: 0.72rem; color: #9ca3af; margin-top: 0.1rem; }

.rm-mini-stars { display: flex; gap: 2px; margin-top: 0.2rem; }

.rm-mini-star {
  width: 13px;
  height: 13px;
  color: #e5e7eb;
  flex-shrink: 0;
}

.rm-mini-star svg { display: block; width: 100%; height: 100%; }
.rm-mini-star.rm-filled { color: #f59e0b; }

.rm-review-comment {
  font-size: 0.85rem;
  color: #374151;
  line-height: 1.55;
  margin: 0.5rem 0 0.75rem;
}

.rm-review-cats {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 0.35rem;
}

.rm-rc {
  background: #f9fafb;
  border-radius: 7px;
  padding: 0.3rem 0.35rem;
  text-align: center;
}

.rm-rc-label {
  font-size: 0.62rem;
  color: #9ca3af;
  margin-bottom: 0.1rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rm-rc-val {
  font-size: 0.82rem;
  font-weight: 700;
  color: #374151;
}

/* ── Empty / loading states ── */

.rm-muted {
  font-size: 0.875rem;
  color: #9ca3af;
  text-align: center;
  padding: 1.5rem 0;
}
`;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function _injectStyles() {
  if (document.getElementById('rm-stylesheet')) return;
  const el = document.createElement('style');
  el.id = 'rm-stylesheet';
  el.textContent = _CSS;
  document.head.appendChild(el);
}

function _esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _starSVG() {
  return `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
  </svg>`;
}

function _miniStars(rating) {
  return [1, 2, 3, 4, 5]
    .map(i => `<span class="rm-mini-star${i <= rating ? ' rm-filled' : ''}">${_starSVG()}</span>`)
    .join('');
}

function _fmtDate(iso) {
  return new Date(iso).toLocaleDateString('en-GB', { year: 'numeric', month: 'short', day: 'numeric' });
}

// ─── Component ────────────────────────────────────────────────────────────────

export default class ReviewMediator {
  #cfg;
  #state;

  constructor(config = {}) {
    this.#cfg = {
      mode: 'form',
      apiBase: '',
      containerId: null,
      contractorId: null,
      jobId: null,
      token: null,
      onSuccess: null,
      ...config,
    };

    this.#state = {
      overall: 0,
      ratings: Object.fromEntries(_CATEGORIES.map(c => [c.key, 0])),
      comment: '',
    };
  }

  // ── Public API ─────────────────────────────────────────────

  mount() {
    _injectStyles();

    const root = typeof this.#cfg.containerId === 'string'
      ? document.getElementById(this.#cfg.containerId)
      : this.#cfg.containerId;

    if (!root) throw new Error(`ReviewMediator: container "${this.#cfg.containerId}" not found`);
    root.classList.add('rm-root');

    const { mode } = this.#cfg;
    if (mode === 'form' || mode === 'both') this._mountForm(root);
    if (mode === 'list' || mode === 'both') this._mountList(root);
  }

  // ── Form ───────────────────────────────────────────────────

  _mountForm(root) {
    const card = document.createElement('div');
    card.className = 'rm-card';
    card.innerHTML = this._formHTML();
    root.appendChild(card);
    this._bindForm(card);
  }

  _formHTML() {
    return `
      <div class="rm-header">
        <h2 class="rm-title">Leave a Review</h2>
        <p class="rm-subtitle">Your honest feedback helps other homeowners choose with confidence.</p>
        <span class="rm-trust-badge">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>
          </svg>
          Verified customer review
        </span>
      </div>

      <div class="rm-overall">
        <div class="rm-section-label">Overall Rating</div>
        <div class="rm-stars" data-role="overall-stars">
          ${[1, 2, 3, 4, 5].map(n => `
            <button class="rm-star" data-v="${n}" type="button" aria-label="${n} star${n > 1 ? 's' : ''}">
              ${_starSVG()}
            </button>
          `).join('')}
        </div>
        <div class="rm-star-hint" data-role="star-hint">Tap to rate</div>
      </div>

      <div class="rm-section-label" style="margin-bottom:.75rem">Category Ratings</div>
      <div class="rm-categories">
        ${_CATEGORIES.map(cat => `
          <div class="rm-cat-row" data-cat="${cat.key}">
            <div class="rm-cat-meta">
              <span class="rm-cat-label">${_esc(cat.label)}</span>
              <span class="rm-cat-value" data-role="cat-val">&nbsp;</span>
            </div>
            <div class="rm-track">
              <div class="rm-fill" data-role="cat-fill"></div>
            </div>
            <div class="rm-dots">
              ${[1, 2, 3, 4, 5].map(n => `
                <button class="rm-dot" data-v="${n}" type="button" aria-label="${_esc(cat.label)} ${n}">${n}</button>
              `).join('')}
            </div>
          </div>
        `).join('')}
      </div>

      <div>
        <label class="rm-comment-label">
          Comment <span class="rm-comment-opt">optional</span>
        </label>
        <textarea
          class="rm-textarea"
          data-role="comment"
          placeholder="Describe the work and your overall experience…"
          maxlength="1000"
          rows="3"
        ></textarea>
      </div>

      <div class="rm-footer">
        <span class="rm-privacy">Reviews are linked to verified job completions only.</span>
        <button class="rm-btn" data-role="submit-btn" type="button">
          Submit Review
        </button>
      </div>

      <div data-role="form-alert"></div>
    `;
  }

  _bindForm(card) {
    // Overall stars
    const stars = card.querySelectorAll('.rm-star');
    const hint  = card.querySelector('[data-role="star-hint"]');

    const setOverall = (v) => {
      this.#state.overall = v;
      stars.forEach(s => s.classList.toggle('rm-active', +s.dataset.v <= v));
      hint.textContent = v ? _RATING_LABELS[v] : 'Tap to rate';
    };

    stars.forEach(s => {
      s.addEventListener('click',     () => setOverall(+s.dataset.v));
      s.addEventListener('mouseover', () => {
        stars.forEach(x => x.classList.toggle('rm-active', +x.dataset.v <= +s.dataset.v));
        hint.textContent = _RATING_LABELS[+s.dataset.v];
      });
    });

    card.querySelector('[data-role="overall-stars"]').addEventListener('mouseleave', () => {
      setOverall(this.#state.overall);
    });

    // Category dots
    _CATEGORIES.forEach(cat => {
      const row  = card.querySelector(`[data-cat="${cat.key}"]`);
      const dots = row.querySelectorAll('.rm-dot');
      const fill = row.querySelector('[data-role="cat-fill"]');
      const val  = row.querySelector('[data-role="cat-val"]');

      const setRating = (v) => {
        this.#state.ratings[cat.key] = v;
        dots.forEach(d => d.classList.toggle('rm-active', +d.dataset.v <= v));
        fill.style.width           = `${(v / 5) * 100}%`;
        fill.style.backgroundColor = _BAR_COLOR(v);
        val.textContent  = _RATING_LABELS[v] || '';
        val.className    = `rm-cat-value${v ? ' rm-rated' : ''}`;
      };

      dots.forEach(d => d.addEventListener('click', () => setRating(+d.dataset.v)));
    });

    // Comment
    card.querySelector('[data-role="comment"]')
      .addEventListener('input', (e) => { this.#state.comment = e.target.value; });

    // Submit
    card.querySelector('[data-role="submit-btn"]')
      .addEventListener('click', () => this._handleSubmit(card));
  }

  async _handleSubmit(card) {
    const { overall, ratings, comment } = this.#state;

    if (!overall) {
      this._alert(card, 'error', 'Please select an overall rating.');
      return;
    }
    if (_CATEGORIES.some(c => !ratings[c.key])) {
      this._alert(card, 'error', 'Please rate all five categories before submitting.');
      return;
    }

    const btn = card.querySelector('[data-role="submit-btn"]');
    btn.disabled     = true;
    btn.innerHTML    = '<span class="rm-spinner"></span> Submitting…';

    try {
      const resp = await fetch(`${this.#cfg.apiBase}/reviews`, {
        method: 'POST',
        headers: {
          'Content-Type':  'application/json',
          'Authorization': `Bearer ${this.#cfg.token}`,
        },
        body: JSON.stringify({
          job_id:        this.#cfg.jobId,
          contractor_id: this.#cfg.contractorId,
          overall,
          ...ratings,
          comment: comment.trim() || null,
        }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error ${resp.status}`);
      }

      const review = await resp.json();

      card.innerHTML = `
        <div class="rm-alert rm-success" style="margin:0">
          <strong>Thank you for your review!</strong><br>
          Your feedback helps other homeowners make confident decisions.
        </div>
      `;
      this.#cfg.onSuccess?.(review);

    } catch (err) {
      btn.disabled  = false;
      btn.innerHTML = 'Submit Review';
      this._alert(card, 'error', _esc(err.message));
    }
  }

  _alert(card, type, message) {
    const el = card.querySelector('[data-role="form-alert"]');
    if (el) el.innerHTML = `<div class="rm-alert rm-${type}">${message}</div>`;
  }

  // ── List ───────────────────────────────────────────────────

  _mountList(root) {
    const card = document.createElement('div');
    card.className = 'rm-card';
    if (this.#cfg.mode === 'both') card.style.marginTop = '1.25rem';
    card.innerHTML = '<p class="rm-muted">Loading reviews…</p>';
    root.appendChild(card);
    this._loadList(card);
  }

  async _loadList(card) {
    const { apiBase, contractorId } = this.#cfg;

    try {
      const [reviewsRes, summaryRes] = await Promise.all([
        fetch(`${apiBase}/reviews/contractor/${contractorId}`),
        fetch(`${apiBase}/reviews/summary/${contractorId}`),
      ]);

      if (!reviewsRes.ok || !summaryRes.ok) throw new Error('Failed to fetch reviews');

      const reviews = await reviewsRes.json();
      const summary = await summaryRes.json();

      card.innerHTML = this._listHTML(reviews, summary);

      // Animate summary bars after paint
      requestAnimationFrame(() => {
        _CATEGORIES.forEach(cat => {
          const fill = card.querySelector(`[data-sum="${cat.key}"]`);
          if (fill) fill.style.width = `${(summary[`avg_${cat.key}`] / 5) * 100}%`;
        });
      });

    } catch (err) {
      card.innerHTML = `<div class="rm-alert rm-error" style="margin:0">Could not load reviews. Please try again later.</div>`;
    }
  }

  _listHTML(reviews, summary) {
    const count = summary.review_count;

    return `
      <div class="rm-header">
        <h2 class="rm-title">Customer Reviews</h2>
        ${count > 0 ? `<p class="rm-subtitle">${count} verified review${count !== 1 ? 's' : ''}</p>` : ''}
      </div>

      ${count > 0 ? `
        <div class="rm-list-summary">
          <div>
            <div class="rm-big-score">${summary.avg_overall.toFixed(1)}</div>
            <div class="rm-big-sub">out of 5</div>
          </div>
          <div class="rm-sum-bars">
            ${_CATEGORIES.map(cat => `
              <div class="rm-sum-row">
                <span class="rm-sum-label">${_esc(cat.label)}</span>
                <div class="rm-sum-track">
                  <div class="rm-sum-fill" data-sum="${cat.key}" style="width:0%"></div>
                </div>
                <span class="rm-sum-val">${summary[`avg_${cat.key}`].toFixed(1)}</span>
              </div>
            `).join('')}
          </div>
        </div>
      ` : '<p class="rm-muted">No reviews yet — be the first to leave feedback.</p>'}

      <div class="rm-review-list">
        ${reviews.map(r => `
          <div class="rm-review-card">
            <div class="rm-review-top">
              <div>
                <div class="rm-reviewer">${_esc(r.reviewer_name || 'Anonymous')}</div>
                <div class="rm-mini-stars">${_miniStars(r.overall)}</div>
              </div>
              <div class="rm-review-date">${_fmtDate(r.created_at)}</div>
            </div>
            ${r.comment ? `<p class="rm-review-comment">${_esc(r.comment)}</p>` : ''}
            <div class="rm-review-cats">
              ${_CATEGORIES.map(cat => `
                <div class="rm-rc">
                  <div class="rm-rc-label">${_esc(cat.label.split(' ')[0])}</div>
                  <div class="rm-rc-val">${r[cat.key]}/5</div>
                </div>
              `).join('')}
            </div>
          </div>
        `).join('')}
      </div>
    `;
  }
}
