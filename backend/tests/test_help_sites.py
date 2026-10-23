"""Nearby help sites: distance maths, parsing, caching and the fallback when Overpass is down."""

from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.help_sites import (
    FALLBACK_NOTE,
    NO_LOCATION_NOTE,
    HelpSiteFinder,
    build_query,
    haversine_km,
    parse_sites,
    travel_minutes,
)
from tests.support import OVERPASS_URL, Home, bearer, create_home, overpass_element

OMAHA = (41.2565, -95.9345)


def sent_query(external: respx.MockRouter) -> str:
    """The Overpass QL the app sent in its first (form-encoded) request."""
    body: str = external["overpass"].calls[0].request.content.decode()
    return parse_qs(body)["data"][0]


def test_haversine_matches_known_distance() -> None:
    # Omaha to Lincoln, Nebraska is about 83 km in a straight line.
    assert haversine_km(*OMAHA, 40.8136, -96.7026) == pytest.approx(81.6, abs=1)
    assert haversine_km(*OMAHA, *OMAHA) == 0


def test_travel_minutes_uses_road_factor_speed_and_parking() -> None:
    # 10 km x 1.3 = 13 km of road at 40 km/h = 19.5 min, plus 3 min to park.
    assert travel_minutes(10) == 22
    assert travel_minutes(0) == 3


def test_query_asks_for_useful_places_around_the_point() -> None:
    query = build_query(*OMAHA, 15)
    assert "around:15000,41.256500,-95.934500" in query
    assert "hospital|fire_station|police|library|community_centre" in query
    assert '"social_facility"="shelter"' in query


def test_parse_sorts_powered_places_first_then_by_distance() -> None:
    elements = [
        overpass_element(1, "library", "Library", 41.2570, -95.9345),
        overpass_element(2, "hospital", "Far Hospital", 41.3500, -95.9345),
        overpass_element(3, "fire_station", "Station 1", 41.2700, -95.9345),
        {
            "type": "way",
            "id": 4,
            "center": {"lat": 41.26, "lon": -95.93},
            "tags": {"amenity": "social_facility", "social_facility": "shelter"},
        },
        {"type": "node", "id": 5, "tags": {"amenity": "hospital"}},  # no coordinates: skipped
    ]
    sites = parse_sites(elements, *OMAHA)
    assert [s.name for s in sites] == ["Station 1", "Far Hospital", "Library", "Shelter"]
    assert [s.likely_has_power for s in sites] == [True, True, False, False]
    assert sites[0].id == "osm_node_3"
    assert sites[3].id == "osm_way_4"
    assert sites[3].kind == "shelter"


def test_parse_merges_buildings_of_the_same_place() -> None:
    elements = [
        overpass_element(1, "hospital", "Nebraska Medical Center", 41.2550, -95.9760),
        overpass_element(2, "hospital", "Nebraska Medical Center", 41.2552, -95.9765),
    ]
    assert len(parse_sites(elements, *OMAHA)) == 1


def test_parse_builds_an_address() -> None:
    element = overpass_element(
        1,
        "hospital",
        "Nebraska Medical Center",
        41.255,
        -95.976,
        **{
            "addr:housenumber": "987400",
            "addr:street": "Nebraska Medical Center",
            "addr:city": "Omaha",
            "addr:state": "NE",
        },
    )
    [site] = parse_sites([element], *OMAHA)
    assert site.address == "987400 Nebraska Medical Center, Omaha, NE"


async def test_finder_uses_overpass_and_caches(external: respx.MockRouter) -> None:
    route = external["overpass"]
    route.respond(
        200, json={"elements": [overpass_element(1, "hospital", "UNMC", 41.255, -95.976)]}
    )
    async with httpx.AsyncClient() as http:
        finder = HelpSiteFinder(http, OVERPASS_URL, timeout_seconds=1)
        first = await finder.find(41.2562, -95.9342)
        second = await finder.find(41.2561, -95.9341)  # within the cache's ~100 m rounding
    assert first.source == "openstreetmap"
    assert first.sites[0].name == "UNMC"
    assert second == first
    assert route.call_count == 1
    assert sent_query(external) == build_query(41.2562, -95.9342, 15)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(504),
        httpx.Response(200, text="<html>rate limited</html>"),
        httpx.Response(200, json={"remark": "no elements key"}),
    ],
)
async def test_finder_falls_back_when_overpass_fails(
    external: respx.MockRouter, response: httpx.Response
) -> None:
    external["overpass"].mock(return_value=response)
    async with httpx.AsyncClient() as http:
        result = await HelpSiteFinder(http, OVERPASS_URL, timeout_seconds=1).find(*OMAHA)
    assert result.source == "fallback"
    assert result.sites == []
    assert result.note == FALLBACK_NOTE


async def test_finder_falls_back_on_timeout_and_does_not_cache_failures(
    external: respx.MockRouter,
) -> None:
    route = external["overpass"]
    route.side_effect = httpx.ConnectTimeout("too slow")
    async with httpx.AsyncClient() as http:
        finder = HelpSiteFinder(http, OVERPASS_URL, timeout_seconds=1)
        assert (await finder.find(*OMAHA)).source == "fallback"
        route.side_effect = None
        route.respond(200, json={"elements": []})
        result = await finder.find(*OMAHA)
    assert result.source == "openstreetmap"
    assert result.note is not None and "Try a larger radius" in result.note


async def test_help_sites_endpoint_defaults_to_household_location(
    client: httpx.AsyncClient, home: Home, external: respx.MockRouter
) -> None:
    external["overpass"].respond(
        200, json={"elements": [overpass_element(1, "fire_station", "Station 1", 41.27, -95.93)]}
    )
    _, caregiver_token = await home.join()
    response = await client.get(f"{home.url}/help-sites", headers=bearer(caregiver_token))
    body = response.json()
    assert body["source"] == "openstreetmap"
    assert body["sites"][0]["name"] == "Station 1"
    assert body["sites"][0]["kind"] == "fire_station"
    assert "around:15000,41.256500,-95.934500" in sent_query(external)


async def test_help_sites_endpoint_accepts_another_position(
    client: httpx.AsyncClient, home: Home, external: respx.MockRouter
) -> None:
    external["overpass"].respond(200, json={"elements": []})
    response = await client.get(
        f"{home.url}/help-sites",
        params={"lat": 40.81, "lon": -96.70, "radius_km": 5},
        headers=home.owner,
    )
    assert response.status_code == 200
    assert "around:5000,40.810000,-96.700000" in sent_query(external)


async def test_help_sites_endpoint_without_location(client: httpx.AsyncClient) -> None:
    home = await create_home(client)
    response = await client.get(f"{home.url}/help-sites", headers=home.owner)
    assert response.json() == {"sites": [], "source": "fallback", "note": NO_LOCATION_NOTE}


async def test_help_sites_endpoint_fallback(client: httpx.AsyncClient, home: Home) -> None:
    response = await client.get(f"{home.url}/help-sites", headers=home.owner)
    assert response.status_code == 200
    assert response.json() == {"sites": [], "source": "fallback", "note": FALLBACK_NOTE}


@pytest.mark.parametrize(
    "params", [{"lat": 41.0}, {"lat": 91, "lon": 0}, {"lat": 41, "lon": -95, "radius_km": 80}]
)
async def test_help_sites_endpoint_validation(
    client: httpx.AsyncClient, home: Home, params: dict[str, float]
) -> None:
    response = await client.get(f"{home.url}/help-sites", params=params, headers=home.owner)
    assert response.status_code == 400
