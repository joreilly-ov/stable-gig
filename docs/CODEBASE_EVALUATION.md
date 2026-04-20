# Codebase Evaluation Report — stable-gig
**Date:** March 30, 2026  
**Evaluation Type:** Static code analysis + architecture review  
**Environment:** Python 3.14.3, FastAPI 0.115.5, Supabase, Cloud Run

---

## Executive Summary

**stable-gig** is a well-structured FastAPI marketplace with thoughtful architecture decisions:
- ✅ **Strong transactional & auth patterns** (Supabase RLS, JWT + slowapi rate limiting, Clean Split identity)
- ✅ **Comprehensive test coverage** (447 tests across 29 files, all external deps mocked, conftest setup solid)
- ✅ **Careful data privacy** (double-blind reviews, column-level REVOKE on private_feedback, anonymous Q&A)
- ⚠️ **3 operational/security hot-spots** requiring immediate attention
- 🔍 **2 code-quality debt areas** worth planning

---

## 🚨 Critical & High-Priority Issues

### 1. PERMISSIVE CORS + WILDCARD ORIGINS [SECURITY]
**File:** `backend/main.py` (lines 96–100)  
**Severity:** HIGH  
**Risk:** `allow_origins=["*"]` exposes all endpoints to any cross-origin request

**Current Code:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # ← ANY origin can call your API
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Impact:**
- Lovable PWA legitimately needs CORS, but `["*"]` is overly broad
- Credentials (JWT in `Authorization` header) are still protected by Supabase auth checks
- However, any malicious site can trigger your rate-limited endpoints (brute-force token guessing)
- Photo/video uploads to `POST /analyse/photos` (JWT-protected) are reachable from anywhere

**Recommendation:**
Whitelist known origins instead of allowing all:
```python
allow_origins=[
    "https://stable-gig-374485351183.europe-west1.run.app",
    "https://lovable-frontend-domain.com",  # Add Lovable PWA domain here
]
```

**Effort:** 5 minutes  
**Impact:** Blocks cross-origin brute-force attacks

---

### 2. MISSING INPUT VALIDATION ON COMPLEX PAYLOADS [DATA INTEGRITY]
**Files:** 
- `backend/app/routers/reviews.py` (lines 68–80)
- `backend/app/routers/jobs.py` (lines 50–56)

**Severity:** HIGH  
**Risk:** Malicious homeowners/contractors can inject arbitrary JSON into `jobs.analysis_result` and `reviews.body`

**Evidence:**
```python
# jobs.py line 50
analysis_result: dict | None = Field(
    default=None,
    description="Gemini analysis JSON from POST /analyse or /analyse/photos — stored verbatim.",
)

# If frontend renders this directly: <div>{job.analysis_result}</div> → XSS risk
```

**Impact:**
- If frontend renders `analysis_result` as HTML (e.g., via `dangerouslySetInnerHTML`), stored XSS is possible
- `reviews.body` (max 5000 chars) has no HTML/script tag stripping
- Arbitrary JSON keys could confuse downstream logic

**Recommendation:**
Add Pydantic validators to whitelist allowed keys:
```python
from pydantic import model_validator

class JobCreate(BaseModel):
    # ... other fields ...
    analysis_result: dict | None = None
    
    @model_validator(mode='after')
    def validate_analysis_result(self):
        if self.analysis_result:
            # Ensure it matches the Gemini output schema
            allowed_top_keys = {
                'problem_type', 'urgency', 'description', 'materials_involved',
                'clarifying_questions', 'video_metadata'
            }
            unexpected = set(self.analysis_result.keys()) - allowed_top_keys
            if unexpected:
                raise ValueError(f"Unexpected keys in analysis_result: {unexpected}")
        return self
```

For review bodies, add sanitization:
```python
from markupsafe import escape

class ReviewCreate(BaseModel):
    body: str | None = Field(default=None, max_length=5_000)
    
    @model_validator(mode='after')
    def sanitize_body(self):
        if self.body:
            self.body = escape(self.body)  # Strip HTML/script tags
        return self
```

**Effort:** 15 minutes  
**Impact:** Blocks stored XSS vectors; hardens schema validation

---

### 3. ~~SILENT FAILURE ON OPTIONAL SERVICES~~ ✅ RESOLVED [OPERATIONAL VISIBILITY]
**Files:**
- `backend/app/services/push_service.py`
- `backend/main.py` (startup event + `/config/feature-flags`)

**Severity:** HIGH (was)  
**Risk (was):** If VAPID is misconfigured in production, contractors never receive job notifications—but no alert surfaces

**Resolved:** Both mitigations are now implemented in `main.py`:
- **Startup CRITICAL log** fires if VAPID keys are absent, making production misconfiguration immediately visible in Cloud Run logs.
- **`GET /config/feature-flags`** returns `{"push_notifications_enabled": bool, "stripe_enabled": bool}` so frontends can gracefully degrade when push is off.

---

## ⚠️ Medium-Priority Issues

### 4. ~~NO PERMANENT RECORD OF DELETED REVIEWS / BIDS~~ ✅ RESOLVED [AUDIT TRAIL]
**Files:** 
- `backend/app/routers/reviews.py` – `DELETE /reviews/{id}` implemented (soft-delete)
- `backend/app/routers/bids.py` – `DELETE /jobs/{id}/bids/{bid_id}` implemented (soft-delete)

**Resolved:** Both routers now have soft-delete endpoints. The `bids` and `reviews` tables have `deleted_at TIMESTAMPTZ` and `deleted_by_user_id UUID` columns. Only the original submitter can soft-delete; bids in `accepted` or `rejected` state are immutable. Rows are retained for dispute resolution.

**Impact:**
- Admin cannot investigate disputes without DB backups
- Regulatory compliance may require immutable audit trails
- Race condition: if homeowner deletes a just-submitted review, no trace remains

**Recommendation:**
Implement soft-delete pattern with audit trail:

```sql
-- Migration: add soft-delete columns
ALTER TABLE reviews ADD COLUMN deleted_at TIMESTAMP NULL DEFAULT NULL;
ALTER TABLE reviews ADD COLUMN deleted_by_user_id UUID REFERENCES auth.users(id);

ALTER TABLE bids ADD COLUMN deleted_at TIMESTAMP NULL DEFAULT NULL;
ALTER TABLE bids ADD COLUMN deleted_by_user_id UUID REFERENCES auth.users(id);

-- Update RLS policies to filter out deleted rows
CREATE POLICY "reviews_exclude_deleted" ON reviews
  FOR SELECT
  USING (deleted_at IS NULL);
```

Python endpoint:
```python
@router.delete("/reviews/{review_id}")
async def delete_review(review_id: str, user=Depends(get_current_user)):
    user_id = str(user.id)
    db = get_supabase_admin()
    
    # Verify ownership
    review = db.table("reviews").select("reviewer_id").eq("id", review_id).limit(1).execute()
    if not review.data or review.data[0]["reviewer_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorised")
    
    # Soft delete
    db.table("reviews").update({
        "deleted_at": "now()",
        "deleted_by_user_id": user_id,
    }).eq("id", review_id).execute()
    
    log.info("review_deleted", extra={"user_id": user_id, "review_id": review_id})
    return {"status": "deleted"}
```

**Effort:** 30 minutes  
**Impact:** Enables audit trail + dispute resolution; regulatory compliance

---

### 5. CONTRACTOR IDENTITY ASSUMPTIONS IN CLEAN SPLIT [FRAGILITY]
**File:** `backend/app/routers/bids.py` (lines 76–83)

**Severity:** MEDIUM  
**Risk:** Code assumes `contractors.id = auth.users.id`, but this invariant is enforced only by convention (comment), not schema

**Current Code:**
```python
def _get_contractor_or_403(user_id: str) -> dict:
    """Under Clean Split, contractors.id = auth.users.id — there is no user_id column."""
    res = _db().table("contractors").select("id").eq("id", user_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=403, detail="...")
    return res.data[0]
```

**The issue:** If identity model ever changes (e.g., separate contractor onboarding), all these lookups silently break.

**Recommendation:**
Enforce the invariant at the database schema level:

```sql
-- Add constraint to prevent mismatches
ALTER TABLE contractors
ADD CONSTRAINT contractors_id_matches_auth_users
  CHECK (id IN (SELECT id FROM auth.users));

-- Trigger to auto-create contractor row when user registers
CREATE OR REPLACE FUNCTION public.create_contractor()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.contractors (id)
  VALUES (NEW.id)
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION create_contractor();
```

Update the comment to reference the constraint:
```python
def _get_contractor_or_403(user_id: str) -> dict:
    """Lookup contractor by user_id.
    
    DB constraint ensures contractors.id = auth.users.id (Clean Split identity).
    See migration 004_clean_split.sql for invariant enforcement.
    """
    res = _db().table("contractors").select("id").eq("id", user_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=403, detail="Only registered contractors may place bids")
    return res.data[0]
```

**Effort:** 20 minutes  
**Impact:** Prevents silent bugs if identity model shifts; makes invariant enforceable

---

## 💪 Strengths

### ✅ Well-designed Review System
- **Double-blind enforced at row level**: Trigger-based `reveal_at` timer prevents premature visibility
- **Column-level REVOKE on private_feedback**: Sensitive admin-only field protected at DB layer (migration 008)
- **visible_reviews view**: Hides body/ai_pros_cons until both parties review or 14 days pass
- **AI summary integration**: Claude Haiku automatically extracts Pros/Cons from review text; aggregated summary stored on `contractor_details` (migration 006)

### ✅ Thoughtful Auth & Rate Limiting
- **Per-IP rate limiting via slowapi**: 5/min magic link, 10/min password login, 5/min registration
- **Email enumeration defence**: `/forgot-password` always returns 202 to prevent leak
- **JWT verification**: Delegated to Supabase (no local secret management)
- **Optional user flow**: `get_optional_user()` for endpoints that support both auth'd and anon access

### ✅ Clean Transactional Boundaries
- **Strict status transitions**: Jobs follow `draft → open → awarded → in_progress → completed | cancelled`
- **Atomic bid acceptance**: Accept one bid + reject all others + award job in single PATCH
- **Escrow gate**: Reviews only submittable when `jobs.escrow_status = 'funds_released'`
- **One-bid-per-contractor**: UNIQUE constraint prevents duplicate bids on same job

### ✅ Solid Test Foundation
- **447 tests** across 29 files with comprehensive mocking (all external APIs stubbed in `conftest.py`)
- **Pre-population strategy**: `sys.modules` pre-populated to prevent import failures
- **Error-case coverage**: Tests cover 201/400/403/404/409/422 scenarios
- **No real I/O**: Test suite requires only Supabase credentials (all mocked), no API keys for Gemini/Stripe

### ✅ Anonymous Q&A Pattern
- **Contractor anonymity**: Questions tied to jobs; homeowners see stable "Contractor N" labels
- **Information leakage prevention**: Contractor identity never exposed until after job awarded

### ✅ Graceful Feature Degradation
- **Optional Stripe**: Payment provider missing → 503 returned
- **Optional VAPID**: Push notifications disabled → gracefully logs warning (though visibility could be better; see issue #3)
- **Optional Smarty**: Address autocomplete disabled → users fall back to postcode entry

---

## 📊 Code Quality Assessment

### Frontend Duplication Risk
**Files:**
- `backend/static/index.html` and `frontend/index.html` (manually kept in sync)
- `backend/static/components/ReviewMediator.js` and `frontend/components/ReviewMediator.js`
- `backend/static/components/TradesmanRating.jsx` and `frontend/components/TradesmanRating.jsx`

**Issue:** Four separate copies of the same code. Easy to diverge if edits happen in only one location.

**Recommendation:**
Add a pre-commit hook to enforce sync:
```bash
#!/bin/bash
# .git/hooks/pre-commit
if ! diff -q backend/static/index.html frontend/index.html > /dev/null 2>&1; then
  echo "Error: backend/static/index.html and frontend/index.html are out of sync"
  exit 1
fi
if ! diff -q backend/static/components/ReviewMediator.js frontend/components/ReviewMediator.js > /dev/null 2>&1; then
  echo "Error: ReviewMediator.js files are out of sync"
  exit 1
fi
# ... repeat for TradesmanRating.jsx ...
```

Or build-time check in CI (GitHub Actions):
```yaml
- name: Check frontend sync
  run: |
    diff backend/static/index.html frontend/index.html
    diff backend/static/components/ReviewMediator.js frontend/components/ReviewMediator.js
    diff backend/static/components/TradesmanRating.jsx frontend/components/TradesmanRating.jsx
```

**Effort:** 10 minutes setup  
**Impact:** Prevents deployments of out-of-sync frontends

---

### Missing Error Handling in AI Services
**Files:**
- `backend/app/services/photo_analyzer.py` – assumes Gemini API always succeeds
- `backend/app/services/push_service.py` (lines 56–71) – catches `WebPushException` but logs as warning, not error

**Issue:** Timeouts or transient API failures propagate to the HTTP response without retry logic.

**Recommendation:**
Implement exponential backoff retry + circuit breaker for Gemini calls:
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def analyse_photo_with_retry(file_bytes: bytes, mime_type: str):
    # ... call Gemini API ...
```

**Effort:** 40 minutes  
**Impact:** Improves resilience to transient API failures

---

### Unbounded List Pagination
**Files:**
- `backend/app/routers/questions.py` – `list_job_questions()` returns all questions, no limit
- `backend/app/routers/bids.py` (line ~160) – `@router.get("/me/bids")` returns all contractor bids, no pagination
- `backend/app/routers/notifications.py` – no list endpoint (minor)

**Issue:** For jobs with 1000+ questions/bids, response becomes huge; memory bloat on server and client.

**Recommendation:**
Add pagination parameters:
```python
from fastapi import Query

@app.get("/jobs/{job_id}/questions")
async def list_job_questions(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user=Depends(get_current_user),
):
    db = get_supabase_admin()
    offset = (page - 1) * page_size
    
    res = (
        db.table("job_questions")
        .select("*", count="exact")
        .eq("job_id", job_id)
        .range(offset, offset + page_size - 1)
        .order("created_at", desc=True)
        .execute()
    )
    
    return {
        "data": res.data,
        "total": res.count,
        "page": page,
        "page_size": page_size,
        "pages": (res.count + page_size - 1) // page_size,
    }
```

**Effort:** 20 minutes per endpoint  
**Impact:** Prevents memory bloat; improves UX with faster responses

---

## 📈 Test Coverage Summary

| Component | Tests | Coverage | Gap |
|-----------|-------|----------|-----|
| Photo analyzer service | 32 | Sharpness, preprocessing, resize logic | Edge cases on malformed EXIF |
| Photo analysis router | 30 | Request validation, error→HTTP mapping | Video-specific tests (separate `analyse.py`) |
| Jobs + bids router | 30 | Full lifecycle, status transitions, auth guards | Concurrent bid acceptance (race condition detection) |
| Reviews router | 14 | private_feedback stripping, list/summary, duplicate detection | Soft-delete endpoint coverage |
| Questions router | 13 | Anonymization, owner answers, auth | Pagination boundaries |
| Notifications router | 8 | VAPID config check, subscribe/unsubscribe | Real push delivery simulation |
| Push service | 9 | Dead-subscription cleanup, no-contractors skip | Circuit breaker behavior |
| Contractor matcher service | 25 | Profile embedding, semantic ranking, expertise fallback | Multi-contractor ranking order |
| Escrow service | 35 | Payment intent, held, transfer, refund | Stripe webhook handling |
| RFP generator service | 27 | Prompt building, cost validation, Gemini call shape | Multi-photo evidence attachment |
| **TOTAL** | **447** | **High** | **See gaps above** |

---

## 🎯 Prioritized Action Plan

| Priority | Issue | Action | Effort | Impact | Status |
|----------|-------|--------|--------|--------|--------|
| **URGENT** | CORS wildcard | Whitelist known origins | 5 min | Blocks cross-origin brute-force | Open |
| **URGENT** | VAPID silent fail | Add startup health check + feature flags | 10 min | Prevents prod notifications blackhole | ✅ Done |
| **HIGH** | Input validation | Add Pydantic validators to `JobCreate` + `ReviewCreate` | 15 min | Blocks stored XSS | Open |
| **HIGH** | Audit trail | Implement soft-delete for reviews/bids | 30 min | Enables dispute resolution | ✅ Done |
| **MEDIUM** | Identity fragility | Add DB constraints + trigger for Clean Split | 20 min | Enforces invariant | Open |
| **MEDIUM** | Frontend sync | Add pre-commit / CI check for index.html + components | 10 min | Prevents deploy divergence | Open |
| **MEDIUM** | Pagination | Add `page` / `page_size` to list endpoints | 20 min each | Prevents memory bloat | Open |
| **LOW** | API resilience | Add exponential backoff retry for Gemini | 40 min | Handles transient failures | Open |

---

## 📋 Deployment Checklist

Before next Cloud Run deploy:
- [ ] Fix CORS origins (remove `["*"]`)
- [ ] Add startup VAPID health check
- [ ] Validate `analysis_result` dict in `JobCreate`
- [ ] Sanitize `reviews.body` with `markupsafe.escape()`
- [ ] Run full test suite: `pytest -v`
- [ ] Check for any pre-commit hook violations
- [ ] Verify frontend files are synced (`backend/static/` ↔ `frontend/`)
- [ ] Test in staging environment with VAPID keys set

---

## 🔗 Related Documentation

- [CustomerReviews.md](CustomerReviews.md) – Full review system schema & RLS details
- [CLAUDE.md](../CLAUDE.md) – Project overview & deployment instructions
- `backend/supabase/migrations/` – Migration history & schema constraints

---

**Generated:** 2026-03-30  
**Reviewed by:** Static code analysis (no runtime execution possible due to environment constraints)
