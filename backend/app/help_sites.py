"""Nearby places that may have power (hospitals, fire stations, libraries...), from OpenStreetMap.

We ask the public Overpass API for the places around a point, estimate how far and how long a
drive each one is, and sort places that usually have backup generators first. Results are cached,
and if Overpass can't be reached the caller gets an empty `fallback` answer with advice instead of
an error: in an outage, a slow map service must never break the app.
"""

import logging
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from cachetools import TTLCache

from app.schemas import HelpSite, HelpSiteKind

logger = logging.getLogger(__name__)

ROAD_FACTOR = 1.3
"""Roads wind: driving distance is roughly 1.3x the straight-line distance."""
AVERAGE_SPEED_KMH = 40.0
PARKING_MINUTES = 3.0
MAX_SITES = 25
POWERED_KINDS: frozenset[str] = frozenset({"hospital", "fire_station", "police"})
"""Hospitals, fire and police stations are required to keep working, so they have generators."""

_AMENITY_KINDS: dict[str, HelpSiteKind] = {
    "hospital": "hospital",
    "fire_station": "fire_station",
    "police": "police",
    "library": "library",
    "community_centre": "community_centre",
}


_DISPLAY_NAMES: dict[str, str] = {
    "hospital": "Hospital",
    "fire_station": "Fire station",
    "police": "Police station",
    "library": "Library",
    "community_centre": "Community center",
    "shelter": "Shelter",
    "other": "Public building",
}


@dataclass(frozen=True, slots=True)
class HelpSitesResult:
    """Sites plus where they came from."""

    sites: list[HelpSite]
    source: Literal["openstreetmap", "fallback"]
    note: str | None = None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    earth_radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def travel_minutes(distance_km: float) -> int:
    """Rough driving time: winding roads at city speeds, plus a few minutes to park."""
    return round(distance_km * ROAD_FACTOR / AVERAGE_SPEED_KMH * 60 + PARKING_MINUTES)


def build_query(lat: float, lon: float, radius_km: float) -> str:
    """Overpass QL for the useful places within `radius_km` of a point."""
    around = f"(around:{round(radius_km * 1000)},{lat:.6f},{lon:.6f})"
    amenities = "|".join(_AMENITY_KINDS)
    return (
        "[out:json][timeout:20];("
        f'nwr["amenity"~"^({amenities})$"]{around};'
        f'nwr["amenity"="social_facility"]["social_facility"="shelter"]{around};'
        ");out center tags 300;"
    )


def _kind(tags: Mapping[str, str]) -> HelpSiteKind:
    amenity = tags.get("amenity", "")
    if amenity == "social_facility" and tags.get("social_facility") == "shelter":
        return "shelter"
    return _AMENITY_KINDS.get(amenity, "other")


def _address(tags: Mapping[str, str]) -> str | None:
    street = " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p)
    region = " ".join(p for p in (tags.get("addr:state"), tags.get("addr:postcode")) if p)
    parts = [p for p in (street, tags.get("addr:city"), region) if p]
    return ", ".join(parts) or None


def parse_sites(elements: Iterable[Mapping[str, Any]], lat: float, lon: float) -> list[HelpSite]:
    """Turn Overpass elements into sorted, de-duplicated help sites."""
    sites: list[HelpSite] = []
    for element in elements:
        point = element if "lat" in element else element.get("center") or {}
        if "lat" not in point or "lon" not in point:
            continue
        tags = element.get("tags") or {}
        kind = _kind(tags)
        site_lat, site_lon = float(point["lat"]), float(point["lon"])
        distance = haversine_km(lat, lon, site_lat, site_lon)
        sites.append(
            HelpSite(
                id=f"osm_{element.get('type', 'node')}_{element.get('id', 0)}",
                name=tags.get("name") or _DISPLAY_NAMES[kind],
                kind=kind,
                lat=round(site_lat, 6),
                lon=round(site_lon, 6),
                distance_km=round(distance, 1),
                travel_minutes=travel_minutes(distance),
                likely_has_power=kind in POWERED_KINDS,
                address=_address(tags),
            )
        )
    # Most useful first (places that usually have generators), then nearest.
    sites.sort(key=lambda s: (not s.likely_has_power, s.distance_km))
    return _deduplicate(sites)[:MAX_SITES]


def _deduplicate(sites: list[HelpSite]) -> list[HelpSite]:
    """Drop repeats: OpenStreetMap often maps a hospital as several buildings with one name."""
    kept: list[HelpSite] = []
    for site in sites:
        key = re.sub(r"[^a-z0-9]", "", site.name.lower())
        duplicate = any(
            other.kind == site.kind
            and re.sub(r"[^a-z0-9]", "", other.name.lower()) == key
            and haversine_km(other.lat, other.lon, site.lat, site.lon) < 0.5
            for other in kept
        )
        if not duplicate:
            kept.append(site)
    return kept


FALLBACK_NOTE = (
    "We couldn't reach OpenStreetMap to look up nearby places right now. Hospitals and fire "
    "stations usually have backup generators. In an emergency, call 911."
)
NO_LOCATION_NOTE = (
    "Add your home's address or location to see nearby places that may have power. "
    "In an emergency, call 911."
)


class HelpSiteFinder:
    """Looks up help sites with Overpass, with caching and a graceful fallback."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        overpass_url: str,
        *,
        timeout_seconds: float,
        cache_ttl_seconds: float = 6 * 3600,
    ) -> None:
        self._http = http
        self._url = overpass_url
        self._timeout = timeout_seconds
        self._cache: TTLCache[tuple[float, float, float], HelpSitesResult] = TTLCache(
            maxsize=512, ttl=cache_ttl_seconds
        )

    async def find(self, lat: float, lon: float, radius_km: float = 15.0) -> HelpSitesResult:
        """Help sites around a point, sorted by usefulness then distance. Never raises."""
        # About 100 m of rounding lets nearby lookups share the cache.
        key = (round(lat, 3), round(lon, 3), round(radius_km, 1))
        if (cached := self._cache.get(key)) is not None:
            return cached
        try:
            response = await self._http.post(
                self._url,
                data={"data": build_query(lat, lon, radius_km)},
                timeout=self._timeout,
            )
            response.raise_for_status()
            elements = response.json()["elements"]
            sites = parse_sites(elements, lat, lon)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Overpass lookup failed (%s); returning fallback", exc)
            return HelpSitesResult(sites=[], source="fallback", note=FALLBACK_NOTE)

        note = None
        if not sites:
            note = (
                f"No hospitals, fire stations or similar places were found within "
                f"{radius_km:g} km. Try a larger radius. In an emergency, call 911."
            )
        result = HelpSitesResult(sites=sites, source="openstreetmap", note=note)
        self._cache[key] = result
        return result
