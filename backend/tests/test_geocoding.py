"""Geocoding: Census first, Nominatim as a fallback, never raising."""

import httpx
import respx

from app.geocoding import Geocoder, GeocodeResult
from tests.support import CENSUS_URL, NOMINATIM_URL

CENSUS_MATCH = {
    "result": {
        "addressMatches": [
            {"coordinates": {"x": -95.9345, "y": 41.2565}, "addressComponents": {"state": "NE"}}
        ]
    }
}


def geocoder(http: httpx.AsyncClient) -> Geocoder:
    return Geocoder(http, census_url=CENSUS_URL, nominatim_url=NOMINATIM_URL, timeout_seconds=1)


async def test_census_match(external: respx.MockRouter) -> None:
    external["census"].respond(200, json=CENSUS_MATCH)
    async with httpx.AsyncClient() as http:
        coder = geocoder(http)
        result = await coder.geocode("1200 Douglas St, Omaha, NE")
        again = await coder.geocode("  1200 douglas st,  omaha, ne ")
    assert result == GeocodeResult(lat=41.2565, lon=-95.9345, state="NE", source="census")
    assert again == result
    assert external["census"].call_count == 1  # cached
    params = external["census"].calls[0].request.url.params
    assert params["benchmark"] == "Public_AR_Current"


async def test_falls_back_to_nominatim(external: respx.MockRouter) -> None:
    external["census"].respond(200, json={"result": {"addressMatches": []}})
    external["nominatim"].respond(
        200, json=[{"lat": "40.8", "lon": "-96.7", "address": {"ISO3166-2-lvl4": "US-NE"}}]
    )
    async with httpx.AsyncClient() as http:
        result = await geocoder(http).geocode("Lincoln, NE")
    assert result == GeocodeResult(lat=40.8, lon=-96.7, state="NE", source="nominatim")
    assert external["nominatim"].calls[0].request.url.params["countrycodes"] == "us"


async def test_returns_none_when_both_fail(external: respx.MockRouter) -> None:
    async with httpx.AsyncClient() as http:
        assert await geocoder(http).geocode("Nowhere") is None


async def test_state_for_coordinates(external: respx.MockRouter) -> None:
    external["nominatim"].respond(200, json={"address": {"ISO3166-2-lvl4": "US-IA"}})
    async with httpx.AsyncClient() as http:
        assert await geocoder(http).state_for(41.26, -95.86) == "IA"
    assert external["nominatim"].calls[0].request.url.path == "/reverse"
