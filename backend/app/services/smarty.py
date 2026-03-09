"""
Smarty (SmartyStreets) address utilities.

Two endpoints:
- US Autocomplete Pro — street-level suggestions filtered to a ZIP code.
- US ZIP Code API    — city/state lookup from a 5-digit ZIP code.

Both return empty results gracefully when the service is not configured.
"""

import httpx
from app.config import settings

_AUTOCOMPLETE_URL = "https://us-autocomplete-pro.api.smartystreets.com/lookup"
_ZIP_URL = "https://us-zipcode.api.smartystreets.com/lookup"
_TIMEOUT = 5.0


def _auth_params() -> dict:
    return {"auth-id": settings.smarty_auth_id, "auth-token": settings.smarty_auth_token}


async def autocomplete_address(search: str, zip_code: str | None = None) -> list[dict]:
    """Return up to 10 US address suggestions for *search*.

    Optionally restricted to *zip_code* (5-digit US ZIP).
    Returns an empty list if the address service is not configured.
    """
    if not settings.smarty_auth_id:
        return []

    params = {**_auth_params(), "search": search, "max_results": 10, "source": "postal"}
    if zip_code:
        params["include_only_zip_codes"] = zip_code

    async with httpx.AsyncClient() as client:
        resp = await client.get(_AUTOCOMPLETE_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()

    suggestions = []
    for s in resp.json().get("suggestions", []):
        street = s.get("street_line", "")
        city = s.get("city", "")
        state = s.get("state", "")
        zipcode = s.get("zipcode", "")
        suggestions.append(
            {
                "street_line": street,
                "city": city,
                "state": state,
                "zipcode": zipcode,
                "display": f"{street}, {city}, {state} {zipcode}".strip(", "),
            }
        )
    return suggestions


async def lookup_zip(zip_code: str) -> dict | None:
    """Return city and state for a US 5-digit ZIP code, or None if not found."""
    if not settings.smarty_auth_id:
        return None

    params = {**_auth_params(), "zipcode": zip_code}
    async with httpx.AsyncClient() as client:
        resp = await client.get(_ZIP_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()

    results = resp.json()
    if not results:
        return None

    city_states = results[0].get("city_states", [])
    if not city_states:
        return None

    # Prefer the mailable city name
    primary = next((cs for cs in city_states if cs.get("mailable_city")), city_states[0])
    return {
        "city": primary.get("city", ""),
        "state": primary.get("state_abbreviation", ""),
        "zipcode": zip_code,
    }
