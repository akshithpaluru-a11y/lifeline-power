"""Create a demo household: `python -m app.seed`.

The Reyes home in Omaha: Luis, a grandfather on a home ventilator with a spare battery, also uses
an oxygen concentrator and a CPAP at night. The family has a portable power station, and his
granddaughter Maya has joined as a caregiver. The script prints the ids and tokens needed to try
the API, the live stream and a drill.
"""

import asyncio
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings
from app.db import Database
from app.events import EventBroker
from app.geocoding import Geocoder
from app.schemas import BackupCreate, DeviceCreate, HouseholdCreate, JoinRequest
from app.services.households import HouseholdService
from app.timeutil import utcnow


@dataclass(frozen=True, slots=True)
class DemoHousehold:
    """What the seed created."""

    household_id: str
    owner_token: str
    caregiver_token: str
    join_code: str


async def seed(settings: Settings) -> DemoHousehold:
    """Create the demo household through the same service the API uses."""
    settings.db.parent.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_url)
    await db.create_tables()
    try:
        async with httpx.AsyncClient() as http, db.sessions() as session:
            # Coordinates and state are given, so the geocoder is never actually called.
            geocoder = Geocoder(
                http,
                census_url=settings.census_geocoder_url,
                nominatim_url=settings.nominatim_url,
                timeout_seconds=settings.http_timeout_seconds,
            )
            service = HouseholdService(session, EventBroker(), geocoder, utcnow)
            household, owner_token = await service.create(
                HouseholdCreate(
                    name="The Reyes home",
                    patient_first_name="Luis",
                    address="Omaha, NE 68102",
                    lat=41.2565,
                    lon=-95.9345,
                    state="NE",
                    utility_name="OPPD",
                )
            )
            for device in (
                DeviceCreate(
                    kind="home_ventilator",
                    label="Grandpa's ventilator",
                    spare_batteries_wh=95,
                    notes="Spare battery is in the hall closet.",
                ),
                DeviceCreate(kind="oxygen_concentrator_home", label="Oxygen concentrator"),
                DeviceCreate(kind="cpap_bipap", label="CPAP (at night)"),
            ):
                await service.add_device(household, device)
            await service.add_backup(
                household,
                BackupCreate(
                    type="power_station",
                    label="Jackery 1000",
                    capacity_wh=1070,
                    max_output_watts=1500,
                ),
            )
            _, caregiver_token, _ = await service.join(
                JoinRequest(
                    join_code=household.join_code, name="Maya", relationship="Granddaughter"
                )
            )
            return DemoHousehold(
                household_id=household.id,
                owner_token=owner_token,
                caregiver_token=caregiver_token,
                join_code=household.join_code,
            )
    finally:
        await db.dispose()


def main() -> None:
    """Seed the database named by `LIFELINE_DB` and print how to use the demo household."""
    settings = get_settings()
    demo = asyncio.run(seed(settings))
    print(f"Demo household created in {settings.db}")
    print(f"  household id     {demo.household_id}")
    print(f"  owner token      {demo.owner_token}")
    print(f"  caregiver token  {demo.caregiver_token}")
    print(f"  join code        {demo.join_code}")
    print()
    print("Try it (with the server running on port 8000):")
    base = f"http://localhost:8000/api/households/{demo.household_id}"
    print(f'  curl -H "Authorization: Bearer {demo.owner_token}" {base}/plan')
    print(f'  curl -N "{base}/stream?token={demo.caregiver_token}"')
    print(
        f'  curl -X POST -H "Authorization: Bearer {demo.owner_token}" '
        f'-H \'Content-Type: application/json\' -d \'{{"source":"drill","speed":300}}\' '
        f"{base}/outages"
    )


if __name__ == "__main__":
    main()
