"""
Integration tests for POST /analyse/photos  (photo_analysis router).

The FastAPI TestClient is used so tests run synchronously; the async
endpoint is handled transparently by Starlette's test infrastructure.

photo_analyzer.analyse() is mocked in every test — no Gemini API calls
are made and no real images need to be processed.

auth dependency (get_optional_user) always returns None so that no
Supabase connection is needed.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# conftest.py has already set all required env vars.
# We build a minimal test app rather than importing main.py because main.py
# has a circular dependency: auth.py imports `limiter` from main.py at
# collection time, which breaks pytest imports.
from app.routers.photo_analysis import router
from app.dependencies import get_optional_user

app = FastAPI()
app.include_router(router)

# ---------------------------------------------------------------------------
# Minimal valid request payload
# ---------------------------------------------------------------------------
_VALID_BODY = {
    "images":      ["data:image/jpeg;base64,/9j/placeholder=="],
    "description": "My kitchen tap has been dripping for two days",
    "trade_category": "plumbing",
}

# Minimal response that photo_analyzer.analyse() would return
_MOCK_RESULT = {
    "likely_issue":    "Worn tap washer",
    "urgency_score":   3,
    "required_tools":  ["flat-head screwdriver", "adjustable spanner"],
    "estimated_parts": ["rubber tap washer 12mm"],
    "image_feedback":  [
        {"index": 0, "role": "Wide Shot", "quality": "ok", "note": None}
    ],
    "token_usage_estimate": {
        "prompt_tokens": 900, "completion_tokens": 80, "total_tokens": 980
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def override_auth():
    """Replace the auth dependency with a no-op that returns None."""
    app.dependency_overrides[get_optional_user] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def mock_analyse():
    """Patch photo_analyzer.analyse to return _MOCK_RESULT without side effects."""
    with patch(
        "app.services.photo_analyzer.analyse",
        new_callable=AsyncMock,
        return_value=dict(_MOCK_RESULT),
    ) as m:
        yield m


# ===========================================================================
# Request validation
# ===========================================================================

class TestRequestValidation:
    """
    Pydantic model validation happens before the endpoint body runs,
    so these tests confirm the contract without needing the service layer.
    """

    def test_empty_images_list_rejected(self, client):
        body = {**_VALID_BODY, "images": []}
        r = client.post("/analyse/photos", json=body)
        assert r.status_code == 422

    def test_six_images_rejected(self, client):
        body = {**_VALID_BODY, "images": ["data:image/jpeg;base64,x=="] * 6}
        r = client.post("/analyse/photos", json=body)
        assert r.status_code == 422

    def test_description_too_short_rejected(self, client):
        body = {**_VALID_BODY, "description": "too short"}   # < 10 chars
        r = client.post("/analyse/photos", json=body)
        assert r.status_code == 422

    def test_description_too_long_rejected(self, client):
        body = {**_VALID_BODY, "description": "x" * 1_001}
        r = client.post("/analyse/photos", json=body)
        assert r.status_code == 422

    def test_invalid_trade_category_rejected(self, client):
        body = {**_VALID_BODY, "trade_category": "carpentry"}
        r = client.post("/analyse/photos", json=body)
        assert r.status_code == 422

    def test_missing_description_rejected(self, client):
        body = {k: v for k, v in _VALID_BODY.items() if k != "description"}
        r = client.post("/analyse/photos", json=body)
        assert r.status_code == 422

    def test_missing_images_rejected(self, client):
        body = {k: v for k, v in _VALID_BODY.items() if k != "images"}
        r = client.post("/analyse/photos", json=body)
        assert r.status_code == 422

    @pytest.mark.parametrize("category", [
        "plumbing", "electrical", "structural", "damp", "roofing", "general",
    ])
    def test_all_valid_trade_categories_accepted(self, client, mock_analyse, category):
        body = {**_VALID_BODY, "trade_category": category}
        r = client.post("/analyse/photos", json=body)
        assert r.status_code != 422, f"'{category}' should be a valid category"

    def test_no_trade_category_accepted(self, client, mock_analyse):
        body = {**_VALID_BODY, "trade_category": None}
        r = client.post("/analyse/photos", json=body)
        assert r.status_code != 422

    def test_five_images_accepted(self, client, mock_analyse):
        body = {**_VALID_BODY, "images": ["data:image/jpeg;base64,x=="] * 5}
        r = client.post("/analyse/photos", json=body)
        assert r.status_code != 422


# ===========================================================================
# Error handling
# ===========================================================================

class TestErrorHandling:
    """
    The endpoint maps service-layer exceptions to specific HTTP status codes
    and must never leak internal details in 5xx responses.
    """

    def test_value_error_from_service_returns_422(self, client):
        with patch(
            "app.services.photo_analyzer.analyse",
            new_callable=AsyncMock,
            side_effect=ValueError("All images were blurry"),
        ):
            r = client.post("/analyse/photos", json=_VALID_BODY)
        assert r.status_code == 422
        assert "blurry" in r.json()["detail"]

    def test_quota_error_returns_429(self, client):
        with patch(
            "app.services.photo_analyzer.analyse",
            new_callable=AsyncMock,
            side_effect=Exception("429 Too Many Requests from Gemini"),
        ):
            r = client.post("/analyse/photos", json=_VALID_BODY)
        assert r.status_code == 429

    def test_rate_limit_error_returns_429(self, client):
        with patch(
            "app.services.photo_analyzer.analyse",
            new_callable=AsyncMock,
            side_effect=Exception("rate limit exceeded for this model"),
        ):
            r = client.post("/analyse/photos", json=_VALID_BODY)
        assert r.status_code == 429

    def test_quota_keyword_returns_429(self, client):
        with patch(
            "app.services.photo_analyzer.analyse",
            new_callable=AsyncMock,
            side_effect=Exception("quota exceeded for project"),
        ):
            r = client.post("/analyse/photos", json=_VALID_BODY)
        assert r.status_code == 429

    def test_generic_exception_returns_500(self, client):
        with patch(
            "app.services.photo_analyzer.analyse",
            new_callable=AsyncMock,
            side_effect=Exception("unexpected internal failure"),
        ):
            r = client.post("/analyse/photos", json=_VALID_BODY)
        assert r.status_code == 500

    def test_500_does_not_leak_internal_details(self, client):
        """The 500 detail must be a generic customer-facing string."""
        with patch(
            "app.services.photo_analyzer.analyse",
            new_callable=AsyncMock,
            side_effect=Exception("db password: hunter2"),
        ):
            r = client.post("/analyse/photos", json=_VALID_BODY)
        assert r.status_code == 500
        assert "hunter2" not in r.json()["detail"]
        assert "password" not in r.json()["detail"]


# ===========================================================================
# Happy path
# ===========================================================================

class TestHappyPath:
    """Confirm the endpoint returns 200 with the correct response shape."""

    def test_valid_request_returns_200(self, client, mock_analyse):
        r = client.post("/analyse/photos", json=_VALID_BODY)
        assert r.status_code == 200

    def test_response_contains_all_required_fields(self, client, mock_analyse):
        r = client.post("/analyse/photos", json=_VALID_BODY)
        data = r.json()
        assert "likely_issue"         in data
        assert "urgency_score"        in data
        assert "required_tools"       in data
        assert "estimated_parts"      in data
        assert "image_feedback"       in data
        assert "token_usage_estimate" in data

    def test_urgency_score_is_integer_in_range(self, client, mock_analyse):
        r = client.post("/analyse/photos", json=_VALID_BODY)
        score = r.json()["urgency_score"]
        assert isinstance(score, int)
        assert 1 <= score <= 10

    def test_urgency_score_clamped_below_minimum(self, client):
        """Router clamps urgency_score=0 → 1."""
        result = dict(_MOCK_RESULT, urgency_score=0)
        with patch("app.services.photo_analyzer.analyse",
                   new_callable=AsyncMock, return_value=result):
            r = client.post("/analyse/photos", json=_VALID_BODY)
        assert r.json()["urgency_score"] == 1

    def test_urgency_score_clamped_above_maximum(self, client):
        """Router clamps urgency_score=99 → 10."""
        result = dict(_MOCK_RESULT, urgency_score=99)
        with patch("app.services.photo_analyzer.analyse",
                   new_callable=AsyncMock, return_value=result):
            r = client.post("/analyse/photos", json=_VALID_BODY)
        assert r.json()["urgency_score"] == 10

    def test_image_feedback_array_present(self, client, mock_analyse):
        r = client.post("/analyse/photos", json=_VALID_BODY)
        feedback = r.json()["image_feedback"]
        assert isinstance(feedback, list)
        assert len(feedback) == 1
        assert feedback[0]["role"]    == "Wide Shot"
        assert feedback[0]["quality"] == "ok"

    def test_token_usage_fields_present(self, client, mock_analyse):
        r = client.post("/analyse/photos", json=_VALID_BODY)
        usage = r.json()["token_usage_estimate"]
        assert "prompt_tokens"     in usage
        assert "completion_tokens" in usage
        assert "total_tokens"      in usage

    def test_analyse_called_with_correct_args(self, client, mock_analyse):
        """Confirm the router passes request fields through unchanged."""
        client.post("/analyse/photos", json=_VALID_BODY)
        mock_analyse.assert_called_once_with(
            images=_VALID_BODY["images"],
            description=_VALID_BODY["description"],
            trade_category=_VALID_BODY["trade_category"],
        )

    def test_no_trade_category_passed_as_none(self, client, mock_analyse):
        body = {**_VALID_BODY, "trade_category": None}
        client.post("/analyse/photos", json=body)
        _, kwargs = mock_analyse.call_args
        assert kwargs["trade_category"] is None
