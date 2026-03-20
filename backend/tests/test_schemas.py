"""Unit tests for app/models/schemas.py (Pydantic model validators).

Coverage
--------
ProfileUpdate:
  - postcode: valid 5-digit ZIP accepted
  - postcode: valid 5+4 ZIP (90210-1234) accepted
  - postcode: None accepted (field is optional)
  - postcode: invalid format raises ValidationError
  - state: valid 2-letter abbreviation accepted and uppercased
  - state: lowercase input normalised to uppercase
  - state: None accepted (field is optional)
  - state: invalid abbreviation raises ValidationError

UserMetadataUpdate:
  - trade_interests: all valid categories accepted
  - trade_interests: invalid category raises ValidationError
  - trade_interests: None accepted
  - trade_interests: mixed valid/invalid raises ValidationError

UserMetadataResponse:
  - trade_interests: None from DB coerced to empty list

Auth schemas:
  - MagicLinkRequest rejects invalid email
  - PasswordAuthRequest accepts any password string

AddressSuggestion / ZipLookupResponse:
  - Fields populated correctly
"""

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    AddressSuggestion,
    MagicLinkRequest,
    PasswordAuthRequest,
    ProfileUpdate,
    UserMetadataResponse,
    UserMetadataUpdate,
    ZipLookupResponse,
)


# ---------------------------------------------------------------------------
# ProfileUpdate — postcode validator
# ---------------------------------------------------------------------------

class TestProfileUpdatePostcode:
    def test_valid_5_digit_zip(self):
        p = ProfileUpdate(postcode="90210")
        assert p.postcode == "90210"

    def test_valid_5_plus_4_zip(self):
        p = ProfileUpdate(postcode="90210-1234")
        assert p.postcode == "90210-1234"

    def test_none_accepted(self):
        p = ProfileUpdate(postcode=None)
        assert p.postcode is None

    def test_missing_field_is_none(self):
        p = ProfileUpdate()
        assert p.postcode is None

    def test_4_digit_zip_rejected(self):
        with pytest.raises(ValidationError):
            ProfileUpdate(postcode="9021")

    def test_6_digit_zip_rejected(self):
        with pytest.raises(ValidationError):
            ProfileUpdate(postcode="902101")

    def test_letters_rejected(self):
        with pytest.raises(ValidationError):
            ProfileUpdate(postcode="ABCDE")

    def test_partial_zip_plus_4_rejected(self):
        with pytest.raises(ValidationError):
            ProfileUpdate(postcode="9021-1234")  # only 4 digits before dash


# ---------------------------------------------------------------------------
# ProfileUpdate — state validator
# ---------------------------------------------------------------------------

class TestProfileUpdateState:
    def test_valid_uppercase_state(self):
        p = ProfileUpdate(state="CA")
        assert p.state == "CA"

    def test_lowercase_state_normalised(self):
        p = ProfileUpdate(state="ca")
        assert p.state == "CA"

    def test_mixed_case_normalised(self):
        p = ProfileUpdate(state="Tx")
        assert p.state == "TX"

    def test_none_accepted(self):
        p = ProfileUpdate(state=None)
        assert p.state is None

    def test_dc_accepted(self):
        p = ProfileUpdate(state="DC")
        assert p.state == "DC"

    def test_invalid_abbreviation_rejected(self):
        with pytest.raises(ValidationError, match="not a recognised"):
            ProfileUpdate(state="XX")

    def test_full_name_rejected(self):
        with pytest.raises(ValidationError):
            ProfileUpdate(state="California")

    @pytest.mark.parametrize("abbr", ["AL", "AK", "AZ", "FL", "NY", "TX", "WA", "WY"])
    def test_all_sampled_states_accepted(self, abbr):
        p = ProfileUpdate(state=abbr)
        assert p.state == abbr


# ---------------------------------------------------------------------------
# UserMetadataUpdate — trade_interests validator
# ---------------------------------------------------------------------------

class TestUserMetadataUpdateTradeInterests:
    @pytest.mark.parametrize(
        "interests",
        [
            ["plumbing"],
            ["electrical"],
            ["structural"],
            ["damp"],
            ["roofing"],
            ["general"],
            ["plumbing", "electrical"],
            [],
        ],
    )
    def test_valid_categories_accepted(self, interests):
        m = UserMetadataUpdate(trade_interests=interests)
        assert m.trade_interests == interests

    def test_none_accepted(self):
        m = UserMetadataUpdate(trade_interests=None)
        assert m.trade_interests is None

    def test_invalid_category_rejected(self):
        with pytest.raises(ValidationError, match="Invalid trade categories"):
            UserMetadataUpdate(trade_interests=["carpentry"])

    def test_mixed_valid_invalid_rejected(self):
        with pytest.raises(ValidationError):
            UserMetadataUpdate(trade_interests=["plumbing", "carpentry"])


# ---------------------------------------------------------------------------
# UserMetadataResponse — null-to-empty-list coercion
# ---------------------------------------------------------------------------

class TestUserMetadataResponse:
    def test_null_trade_interests_becomes_empty_list(self):
        r = UserMetadataResponse(
            id="uid",
            username="alice",
            bio=None,
            trade_interests=None,   # DB returns null
            setup_complete=False,
            updated_at="2024-01-01T00:00:00",
        )
        assert r.trade_interests == []

    def test_populated_trade_interests_preserved(self):
        r = UserMetadataResponse(
            id="uid",
            username="alice",
            bio=None,
            trade_interests=["plumbing"],
            setup_complete=True,
            updated_at="2024-01-01T00:00:00",
        )
        assert r.trade_interests == ["plumbing"]


# ---------------------------------------------------------------------------
# Auth schemas
# ---------------------------------------------------------------------------

class TestAuthSchemas:
    def test_magic_link_accepts_valid_email(self):
        m = MagicLinkRequest(email="user@example.com")
        assert m.email == "user@example.com"

    def test_magic_link_rejects_invalid_email(self):
        with pytest.raises(ValidationError):
            MagicLinkRequest(email="not-an-email")

    def test_password_auth_request_accepts_any_password(self):
        r = PasswordAuthRequest(email="user@example.com", password="x")
        assert r.password == "x"


# ---------------------------------------------------------------------------
# Address schemas
# ---------------------------------------------------------------------------

class TestAddressSchemas:
    def test_address_suggestion_fields(self):
        s = AddressSuggestion(
            street_line="123 Main St",
            city="Springfield",
            state="IL",
            zipcode="62701",
            display="123 Main St, Springfield, IL 62701",
        )
        assert s.display == "123 Main St, Springfield, IL 62701"

    def test_zip_lookup_response_fields(self):
        z = ZipLookupResponse(city="Beverly Hills", state="CA", zipcode="90210")
        assert z.city == "Beverly Hills"
        assert z.state == "CA"
        assert z.zipcode == "90210"
