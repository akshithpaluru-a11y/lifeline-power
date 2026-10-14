"""Regression tests for the map services: the Overpass query isn't truncated, Overpass errors
aren't cached as "nothing nearby", and the geocoder neither waits out its budget on a slow
Census nor remembers a failed lookup as "not found"."""

import asyncio
import re

import httpx
import pytest
import respx

from app.geocoding import Geocoder, GeocodeResult
from app.help_sites import FALLBACK_NOTE, HelpSiteFinder, build_query
from app.services.households import LOCATION_LOOKUP_BUDGET_SECONDS
from tests.support import CENSUS_URL, NOMINATIM_URL, OVERPASS_URL, overpass_element

OMAHA = (41.2565, -95.9345)
CENSUS_MATCH = {
    "result": {
        "addressMatches": [
            {"coordinates": {"x": -95.9345, "y": 41.2565}, "addressComponents": {"state": "NE"}}
        ]
    }
}
NOMINATIM_MATCH = [{"lat": "41.2565", "lon": "-95.9345", "address": {"ISO3166-2-lvl4": "US-NE"}}]


# --- Finding 27: no output limit that drops hospitals mapped as ways ---------------------------


def test_overpass_query_returns_every_match() -> None:
    query = build_query(*OMAHA, 50)
    # A numeric limit on `out` cuts the answer by element type and id, not distance: nodes
    # first, so hospitals mapped as buildings (ways) are the first to go.
    outs = re.findall(r"(?:^|;)\s*(out\b[^;]*);", query)  # output statements, not [out:json]
    assert outs, query
    assert not any(re.search(r"\d", out) for out in outs), outs


# --- Finding 28: Overpass errors reported inside a 200 answer -----------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"elements": [], "remark": "runtime error: Query run out of memory using about 2048 MB"},
        {
            "elements": [overpass_element(1, "library", "Library", 41.257, -95.934)],
            "remark": 'runtime error: Query timed out in "query" at line 1 after 21 seconds.',
        },
        {"elements": [], "remark": "runtime remark: Timeout is 20 and maxsize is 536870912."},
    ],
)
async def test_overpass_error_remark_is_a_failure_and_is_not_cached(
    external: respx.MockRouter, body: dict[str, object]
) -> None:
    route = external["overpass"]
    route.respond(200, json=body)
    async with httpx.AsyncClient() as http:
        finder = HelpSiteFinder(http, OVERPASS_URL, timeout_seconds=1)
        first = await finder.find(*OMAHA)
        route.respond(
            200, json={"elements": [overpass_element(2, "hospital", "UNMC", 41.255, -95.976)]}
        )
        second = await finder.find(*OMAHA)
    assert (first.source, first.sites, first.note) == ("fallback", [], FALLBACK_NOTE)
    assert second.source == "openstreetmap"
    assert [site.name for site in second.sites] == ["UNMC"]
    assert route.call_count == 2


async def test_overpass_answer_without_remark_is_still_cached(external: respx.MockRouter) -> None:
    route = external["overpass"]
    route.respond(200, json={"elements": []})
    async with httpx.AsyncClient() as http:
        finder = HelpSiteFinder(http, OVERPASS_URL, timeout_seconds=1)
        await finder.find(*OMAHA)
        result = await finder.find(*OMAHA)
    assert result.source == "openstreetmap"
    assert route.call_count == 1


# --- Finding 29: a slow Census answer must leave time for Nominatim -----------------------------


def geocoder(http: httpx.AsyncClient, timeout_seconds: float = 8) -> Geocoder:
    return Geocoder(
        http,
        census_url=CENSUS_URL,
        nominatim_url=NOMINATIM_URL,
        timeout_seconds=timeout_seconds,
    )


async def test_slow_census_leaves_time_for_nominatim(external: respx.MockRouter) -> None:
    async def slow_census(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(7)
        return httpx.Response(200, json=CENSUS_MATCH)

    external["census"].side_effect = slow_census
    external["nominatim"].respond(200, json=NOMINATIM_MATCH)
    async with httpx.AsyncClient() as http:
        # The same budget HouseholdService gives a lookup when a household is saved.
        async with asyncio.timeout(LOCATION_LOOKUP_BUDGET_SECONDS):
            result = await geocoder(http).geocode("1200 Douglas St, Omaha, NE")
    assert result == GeocodeResult(lat=41.2565, lon=-95.9345, state="NE", source="nominatim")
    assert external["nominatim"].called


# --- Finding 31: failed lookups aren't remembered as "not found" ---------------------------------


async def test_failed_geocode_is_retried_once_the_services_recover(
    external: respx.MockRouter,
) -> None:
    address = "123 Elm St, Omaha, NE 68102"
    async with httpx.AsyncClient() as http:
        coder = geocoder(http, timeout_seconds=1)
        assert await coder.geocode(address) is None  # both services answer 503
        external["census"].respond(200, json=CENSUS_MATCH)
        result = await coder.geocode(address)
    assert result is not None and result.source == "census"


async def test_census_failure_with_nominatim_miss_is_not_cached(
    external: respx.MockRouter,
) -> None:
    external["nominatim"].respond(200, json=[])
    async with httpx.AsyncClient() as http:
        coder = geocoder(http, timeout_seconds=1)
        assert await coder.geocode("123 Elm St, Omaha, NE") is None  # Census is down
        external["census"].respond(200, json=CENSUS_MATCH)
        assert await coder.geocode("123 Elm St, Omaha, NE") is not None


async def test_a_genuine_no_match_is_still_cached(external: respx.MockRouter) -> None:
    external["census"].respond(200, json={"result": {"addressMatches": []}})
    external["nominatim"].respond(200, json=[])
    async with httpx.AsyncClient() as http:
        coder = geocoder(http, timeout_seconds=1)
        assert await coder.geocode("Nowhere at all") is None
        assert await coder.geocode("nowhere  at all") is None
    assert external["census"].call_count == 1


async def test_failed_reverse_lookup_is_retried(external: respx.MockRouter) -> None:
    async with httpx.AsyncClient() as http:
        coder = geocoder(http, timeout_seconds=1)
        assert await coder.state_for(41.26, -95.86) is None  # Nominatim answers 503
        external["nominatim"].respond(200, json={"address": {"ISO3166-2-lvl4": "US-IA"}})
        assert await coder.state_for(41.26, -95.86) == "IA"
