"""Integration tests for the address router (GET /address/zip, GET /address/autocomplete).

Coverage
--------
GET /address/zip
  - 503 when Smarty credentials not configured
  - 422 when postcode is not a 5-digit US ZIP
  - 404 when ZIP not found
  - 200 with city/state on success

GET /address/autocomplete
  - 503 when Smarty credentials not configured
  - 422 when search is too short (< 3 chars)
  - 422 when postcode has wrong format
  - 200 with suggestions list on success
  - 200 with empty list when no suggestions
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.address import router

app = FastAPI()
app.include_router(router)

client = TestClient(app)

# ---------------------------------------------------------------------------
# Shared Smarty credential patches
# ---------------------------------------------------------------------------

def _with_smarty():
    """Patch settings to simulate Smarty credentials being present."""
    return patch("app.routers.address.settings",
                 smarty_auth_id="id-123", smarty_auth_token="tok-456")


def _without_smarty():
    """Patch settings to simulate missing Smarty credentials."""
    return patch("app.routers.address.settings",
                 smarty_auth_id="", smarty_auth_token="")


# ---------------------------------------------------------------------------
# GET /address/zip
# ---------------------------------------------------------------------------

class TestZipLookup:
    def test_no_smarty_credentials_returns_503(self):
        with _without_smarty():
            resp = client.get("/address/zip", params={"postcode": "90210"})
        assert resp.status_code == 503

    def test_invalid_postcode_format_returns_422(self):
        with _with_smarty():
            resp = client.get("/address/zip", params={"postcode": "9021"})   # 4 digits
        assert resp.status_code == 422

    def test_non_digit_postcode_returns_422(self):
        with _with_smarty():
            resp = client.get("/address/zip", params={"postcode": "ABCDE"})
        assert resp.status_code == 422

    def test_zip_not_found_returns_404(self):
        with (
            _with_smarty(),
            patch("app.routers.address.lookup_zip", new=AsyncMock(return_value=None)),
        ):
            resp = client.get("/address/zip", params={"postcode": "00000"})
        assert resp.status_code == 404

    def test_success_returns_city_state(self):
        result = {"city": "Beverly Hills", "state": "CA", "zipcode": "90210"}
        with (
            _with_smarty(),
            patch("app.routers.address.lookup_zip", new=AsyncMock(return_value=result)),
        ):
            resp = client.get("/address/zip", params={"postcode": "90210"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["city"] == "Beverly Hills"
        assert body["state"] == "CA"
        assert body["zipcode"] == "90210"

    def test_lookup_called_with_postcode(self):
        result = {"city": "Austin", "state": "TX", "zipcode": "73301"}
        with (
            _with_smarty(),
            patch("app.routers.address.lookup_zip", new=AsyncMock(return_value=result)) as mock_lookup,
        ):
            client.get("/address/zip", params={"postcode": "73301"})
        mock_lookup.assert_called_once_with("73301")


# ---------------------------------------------------------------------------
# GET /address/autocomplete
# ---------------------------------------------------------------------------

class TestAddressAutocomplete:
    def test_no_smarty_credentials_returns_503(self):
        with _without_smarty():
            resp = client.get("/address/autocomplete", params={"search": "123 Main"})
        assert resp.status_code == 503

    def test_search_too_short_returns_422(self):
        with _with_smarty():
            resp = client.get("/address/autocomplete", params={"search": "12"})
        assert resp.status_code == 422

    def test_invalid_postcode_format_returns_422(self):
        with _with_smarty():
            resp = client.get(
                "/address/autocomplete",
                params={"search": "123 Main", "postcode": "123"},
            )
        assert resp.status_code == 422

    def test_success_returns_suggestions(self):
        suggestions = [
            {
                "street_line": "123 Main St",
                "city": "Springfield",
                "state": "IL",
                "zipcode": "62701",
                "display": "123 Main St, Springfield, IL 62701",
            }
        ]
        with (
            _with_smarty(),
            patch(
                "app.routers.address.autocomplete_address",
                new=AsyncMock(return_value=suggestions),
            ),
        ):
            resp = client.get(
                "/address/autocomplete", params={"search": "123 Main"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["street_line"] == "123 Main St"

    def test_empty_suggestions_returns_empty_list(self):
        with (
            _with_smarty(),
            patch(
                "app.routers.address.autocomplete_address",
                new=AsyncMock(return_value=[]),
            ),
        ):
            resp = client.get(
                "/address/autocomplete", params={"search": "999 Nowhere"}
            )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_postcode_filter_passed_to_service(self):
        with (
            _with_smarty(),
            patch(
                "app.routers.address.autocomplete_address",
                new=AsyncMock(return_value=[]),
            ) as mock_auto,
        ):
            client.get(
                "/address/autocomplete",
                params={"search": "123 Main", "postcode": "90210"},
            )
        mock_auto.assert_called_once_with("123 Main", "90210")
