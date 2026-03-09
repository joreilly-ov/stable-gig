import re
from pydantic import BaseModel, EmailStr, field_validator

US_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}


# --- Auth ---

class MagicLinkRequest(BaseModel):
    email: EmailStr


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str


# --- Profiles ---

class ProfileUpdate(BaseModel):
    full_name: str | None = None
    postcode: str | None = None
    road_address: str | None = None
    city: str | None = None
    state: str | None = None

    @field_validator("postcode")
    @classmethod
    def postcode_must_be_us_zip(cls, v: str | None) -> str | None:
        if v is not None and not US_ZIP_RE.match(v):
            raise ValueError("Only US ZIP codes accepted (e.g. 90210 or 90210-1234)")
        return v

    @field_validator("state")
    @classmethod
    def state_must_be_us(cls, v: str | None) -> str | None:
        if v is not None and v.upper() not in STATE_ABBREVS:
            raise ValueError(f"'{v}' is not a recognised US state abbreviation")
        return v.upper() if v else v


class ProfileResponse(BaseModel):
    id: str
    full_name: str | None
    postcode: str | None
    road_address: str | None
    city: str | None
    state: str | None
    created_at: str


# --- Address ---

class AddressSuggestion(BaseModel):
    street_line: str
    city: str
    state: str
    zipcode: str
    display: str


class ZipLookupResponse(BaseModel):
    city: str
    state: str
    zipcode: str
