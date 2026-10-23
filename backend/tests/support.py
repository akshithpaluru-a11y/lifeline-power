"""Test helpers: a controllable clock and a thin client for households made through the API."""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.events import Event, Subscription
from app.planner import BackupInput, DeviceInput

OVERPASS_URL = "https://overpass.test/api/interpreter"
NOMINATIM_URL = "https://nominatim.test"
CENSUS_URL = "https://census.test/geocoder"


@dataclass
class FakeClock:
    """A clock that only moves when a test moves it."""

    now: datetime = field(default_factory=lambda: datetime(2026, 10, 20, 18, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> datetime:
        self.now += timedelta(**delta)
        return self.now


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Home:
    """A household created through the API, with its tokens."""

    client: httpx.AsyncClient
    id: str
    owner_token: str
    join_code: str
    data: dict[str, Any]

    @property
    def owner(self) -> dict[str, str]:
        return bearer(self.owner_token)

    @property
    def url(self) -> str:
        return f"/api/households/{self.id}"

    async def add_device(self, **fields: Any) -> dict[str, Any]:
        response = await self.client.post(f"{self.url}/devices", json=fields, headers=self.owner)
        assert response.status_code == 201, response.text
        device: dict[str, Any] = response.json()["device"]
        return device

    async def add_backup(self, **fields: Any) -> dict[str, Any]:
        response = await self.client.post(f"{self.url}/backups", json=fields, headers=self.owner)
        assert response.status_code == 201, response.text
        backup: dict[str, Any] = response.json()["backup"]
        return backup

    async def join(self, name: str = "Maya", **fields: Any) -> tuple[dict[str, Any], str]:
        response = await self.client.post(
            "/api/caregivers/join", json={"join_code": self.join_code, "name": name, **fields}
        )
        assert response.status_code == 201, response.text
        body = response.json()
        return body["caregiver"], body["caregiver_token"]

    async def start_outage(self, **fields: Any) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.url}/outages", json={"source": "manual", **fields}, headers=self.owner
        )
        assert response.status_code == 201, response.text
        outage: dict[str, Any] = response.json()["outage"]
        return outage


async def create_home(client: httpx.AsyncClient, **fields: Any) -> Home:
    body = {"name": "The Reyes home", "patient_first_name": "Luis", **fields}
    response = await client.post("/api/households", json=body)
    assert response.status_code == 201, response.text
    data = response.json()
    household = data["household"]
    return Home(client, household["id"], data["owner_token"], household["join_code"], household)


# --- Planner inputs ------------------------------------------------------------------------

_DEVICE = DeviceInput(
    id="d",
    kind="custom",
    label="d",
    life_sustaining=True,
    draw_watts=100.0,
    saver_draw_watts=None,
    saver_enabled=False,
    duty_cycle=1.0,
    ac_powered=True,
    internal_battery_wh=0.0,
    spare_batteries_wh=0.0,
    charge_percent=100.0,
)
_STATION = BackupInput(
    id="s",
    type="power_station",
    label="s",
    capacity_wh=1000.0,
    max_output_watts=1500.0,
    has_ac_outlet=True,
    charge_percent=100.0,
    fuel_hours=None,
)


def device(id: str = "d", **overrides: Any) -> DeviceInput:
    return replace(_DEVICE, id=id, label=id, **overrides)


def station(id: str = "s", **overrides: Any) -> BackupInput:
    return replace(_STATION, id=id, label=overrides.pop("label", id), **overrides)


def generator(id: str = "g", **overrides: Any) -> BackupInput:
    fields = {
        "type": "generator",
        "capacity_wh": None,
        "max_output_watts": 1800.0,
        "charge_percent": None,
        "fuel_hours": 8.0,
    }
    return station(id, **{**fields, **overrides})


async def collect(subscription: Subscription) -> list[Event]:
    """Every event that has arrived on a subscription (waiting briefly for stragglers)."""
    events: list[Event] = []
    while True:
        try:
            event = await asyncio.wait_for(subscription.next(), 0.05)
        except TimeoutError:
            return events
        assert event is not None, "the stream was closed"
        events.append(event)


def overpass_element(
    osm_id: int, amenity: str, name: str, lat: float, lon: float, **tags: str
) -> dict[str, Any]:
    """An Overpass API node."""
    return {
        "type": "node",
        "id": osm_id,
        "lat": lat,
        "lon": lon,
        "tags": {"amenity": amenity, "name": name, **tags},
    }
