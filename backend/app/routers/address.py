"""Address utility routes.

GET /address/zip?postcode=90210
    → returns city + state for a US 5-digit ZIP code.

GET /address/autocomplete?search=123+Main&postcode=90210
    → returns up to 10 matching US address suggestions.

Both endpoints return a 503 when Smarty credentials are not configured.
"""

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.models.schemas import AddressSuggestion, ZipLookupResponse
from app.services.smarty import autocomplete_address, lookup_zip

router = APIRouter(prefix="/address", tags=["address"])


def _require_smarty():
    if not settings.smarty_auth_id or not settings.smarty_auth_token:
        raise HTTPException(
            status_code=503,
            detail="Address service not configured — set SMARTY_AUTH_ID and SMARTY_AUTH_TOKEN",
        )


@router.get("/zip", response_model=ZipLookupResponse)
async def zip_lookup(
    postcode: str = Query(..., pattern=r"^\d{5}$", description="5-digit US ZIP code"),
):
    _require_smarty()
    result = await lookup_zip(postcode)
    if not result:
        raise HTTPException(status_code=404, detail=f"ZIP code {postcode!r} not found")
    return result


@router.get("/autocomplete", response_model=list[AddressSuggestion])
async def address_autocomplete(
    search: str = Query(..., min_length=3, description="Partial street address"),
    postcode: str | None = Query(default=None, pattern=r"^\d{5}$", description="Restrict to ZIP"),
):
    _require_smarty()
    return await autocomplete_address(search, postcode)
