"""Address <-> coordinates, so households that type an address still get nearby help sites.

Forward lookups try the US Census Bureau geocoder first (free, no key, made for US addresses) and
fall back to OpenStreetMap Nominatim. Reverse lookups (a map pin or phone location -> state) use
Nominatim. Both are time-limited and never raise: a failed lookup just leaves the location
unset, and the plan then suggests adding one. Answers are cached; failures are not, so an
address is looked up again once the services are back.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from cachetools import TTLCache

logger = logging.getLogger(__name__)

_NOMINATIM_MIN_INTERVAL_SECONDS = 1.0
"""Nominatim's usage policy allows at most one request per second."""
CENSUS_TIME_LIMIT_SECONDS = 2.5
"""Longest we wait for the Census geocoder, so that Nominatim still gets its turn within the
few seconds a caller gives the whole lookup (6 s when a household is saved)."""


class _LookupFailed(Exception):
    """A geocoding service couldn't be asked (error, timeout, or an answer we can't read).

    Kept apart from "no match", so that a failure is never cached as "this address doesn't
    exist".
    """


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    """A geocoded address."""

    lat: float
    lon: float
    state: str | None
    source: str


class Geocoder:
    """Census-first geocoder with a Nominatim fallback."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        census_url: str,
        nominatim_url: str,
        timeout_seconds: float,
    ) -> None:
        self._http = http
        self._census_url = census_url.rstrip("/")
        self._nominatim_url = nominatim_url.rstrip("/")
        self._timeout = timeout_seconds
        self._forward: TTLCache[str, GeocodeResult | None] = TTLCache(maxsize=1024, ttl=86400)
        self._reverse: TTLCache[tuple[float, float], str | None] = TTLCache(maxsize=1024, ttl=86400)
        self._nominatim_lock = asyncio.Lock()
        self._nominatim_last_call = 0.0

    async def geocode(self, address: str) -> GeocodeResult | None:
        """Coordinates and state for a free-text US address, or None if it can't be found."""
        key = " ".join(address.lower().split())
        if key in self._forward:
            return self._forward[key]
        result, failed = None, False
        for lookup in (self._census, self._nominatim_search):
            try:
                result = await lookup(address)
            except _LookupFailed as exc:
                logger.warning("Geocoding failed (%s)", exc)
                failed = True
                continue
            if result is not None:
                break
        # "Not found" is only remembered when every service could be asked.
        if result is not None or not failed:
            self._forward[key] = result
        return result

    async def state_for(self, lat: float, lon: float) -> str | None:
        """The 2-letter US state at a point, or None."""
        key = (round(lat, 3), round(lon, 3))
        if key in self._reverse:
            return self._reverse[key]
        try:
            payload = await self._nominatim_get(
                "/reverse",
                {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2", "zoom": "5"},
            )
        except _LookupFailed as exc:
            logger.warning("Reverse geocoding failed (%s)", exc)
            return None
        state = _state_from_nominatim(payload.get("address")) if isinstance(payload, dict) else None
        self._reverse[key] = state
        return state

    async def _census(self, address: str) -> GeocodeResult | None:
        """The Census geocoder's best match, or None if it has none."""
        try:
            async with asyncio.timeout(min(self._timeout, CENSUS_TIME_LIMIT_SECONDS)):
                response = await self._http.get(
                    f"{self._census_url}/locations/onelineaddress",
                    params={
                        "address": address,
                        "benchmark": "Public_AR_Current",
                        "format": "json",
                    },
                    timeout=self._timeout,
                )
            response.raise_for_status()
            matches = response.json()["result"]["addressMatches"]
            if not matches:
                return None
            best = matches[0]
            state = best.get("addressComponents", {}).get("state")
            return GeocodeResult(
                lat=float(best["coordinates"]["y"]),
                lon=float(best["coordinates"]["x"]),
                state=state.upper() if isinstance(state, str) and len(state) == 2 else None,
                source="census",
            )
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise _LookupFailed(f"Census: {exc!r}") from exc

    async def _nominatim_search(self, address: str) -> GeocodeResult | None:
        """Nominatim's best US match, or None if it has none."""
        payload = await self._nominatim_get(
            "/search",
            {
                "q": address,
                "format": "jsonv2",
                "addressdetails": "1",
                "countrycodes": "us",
                "limit": "1",
            },
        )
        if not isinstance(payload, list):
            raise _LookupFailed(f"Nominatim /search: unexpected {type(payload).__name__}")
        if not payload:
            return None
        try:
            best = payload[0]
            return GeocodeResult(
                lat=float(best["lat"]),
                lon=float(best["lon"]),
                state=_state_from_nominatim(best.get("address")),
                source="nominatim",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise _LookupFailed(f"Nominatim /search: {exc!r}") from exc

    async def _nominatim_get(self, path: str, params: dict[str, str]) -> Any:
        """GET a Nominatim endpoint, at most once per second, returning its JSON."""
        async with self._nominatim_lock:
            wait = _NOMINATIM_MIN_INTERVAL_SECONDS - (time.monotonic() - self._nominatim_last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await self._http.get(
                    f"{self._nominatim_url}{path}", params=params, timeout=self._timeout
                )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise _LookupFailed(f"Nominatim {path}: {exc!r}") from exc
            finally:
                self._nominatim_last_call = time.monotonic()


def _state_from_nominatim(address: Any) -> str | None:
    """`US-NE` style ISO code from a Nominatim address block -> `NE`."""
    if not isinstance(address, dict):
        return None
    code = address.get("ISO3166-2-lvl4")
    if isinstance(code, str) and code.startswith("US-") and len(code) == 5:
        return code[3:]
    return None
