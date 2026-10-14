"""Households: creating and changing them, their equipment, caregivers, and planning inputs.

Every change is committed first and announced second (`household_updated`), so nobody watching
the live stream ever sees a change that was then rolled back.

Requests that change the same household at the same moment are kept consistent by SQLite
itself: only one connection can write at a time, and once it has written it keeps that right
until it commits. So each change first writes its own rows, then reads the household again in
the same transaction. That read includes everything other requests committed before, and nobody
else can commit until this request does. Limits (devices, backups, caregivers) are checked on
that fresh read, so racing requests can't get past them, and the household announced and
returned is exactly what ends up in the database, never a copy loaded before someone else's
change.
"""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.auth import hash_token, new_token
from app.errors import conflict, not_found
from app.events import EventBroker, HouseholdData
from app.geocoding import Geocoder
from app.ids import new_id, new_join_code, normalize_join_code
from app.planner import BackupInput, DeviceInput, HouseholdFacts, build_plan
from app.services import equipment
from app.timeutil import Clock

logger = logging.getLogger(__name__)

MAX_DEVICES = 20
MAX_BACKUPS = 8
"""The planner checks every combination of batteries, so keep the count small (2^8 = 256)."""
MAX_CAREGIVERS = 20
_UNKNOWN_JOIN_CODE = "No household has this join code. Check it with the family."
_JOIN_CODE_ATTEMPTS = 20
LOCATION_LOOKUP_BUDGET_SECONDS = 6.0
"""Longest a request waits for an address lookup. Lookups are a convenience, never required."""


async def load_household(session: AsyncSession, household_id: str) -> models.Household:
    """The household with its devices, backups and caregivers (404 if it doesn't exist)."""
    household = await session.get(models.Household, household_id)
    if household is None:
        raise not_found("Household not found.")
    return household


def household_view(household: models.Household) -> schemas.Household:
    """The API representation of a household."""
    return schemas.Household.model_validate(household)


# --- Planner inputs ----------------------------------------------------------------------


def device_input(device: models.Device) -> DeviceInput:
    """A stored device as the planner sees it."""
    return DeviceInput(
        id=device.id,
        kind=device.kind,
        label=device.label,
        life_sustaining=device.life_sustaining,
        draw_watts=device.draw_watts,
        saver_draw_watts=device.saver_draw_watts,
        saver_enabled=device.saver_enabled,
        duty_cycle=device.duty_cycle,
        ac_powered=device.ac_powered,
        internal_battery_wh=device.internal_battery_wh,
        spare_batteries_wh=device.spare_batteries_wh,
        charge_percent=device.charge_percent,
    )


def backup_input(backup: models.Backup) -> BackupInput:
    """A stored backup as the planner sees it."""
    return BackupInput(
        id=backup.id,
        type=backup.type,
        label=backup.label,
        capacity_wh=backup.capacity_wh,
        max_output_watts=backup.max_output_watts,
        has_ac_outlet=backup.has_ac_outlet,
        charge_percent=backup.charge_percent,
        fuel_hours=backup.fuel_hours,
    )


def household_facts(household: models.Household) -> HouseholdFacts:
    """The facts about a household that shape the plan's recommendations."""
    return HouseholdFacts(
        medical_program_registered=household.medical_program_registered,
        caregiver_count=len(household.caregivers),
        has_location=household.lat is not None and household.lon is not None,
        utility_name=household.utility_name,
        state=household.state,
    )


def compute_plan(
    household: models.Household, *, now: datetime, saver_preview: bool = False
) -> schemas.Plan:
    """The household's plan from its saved devices and backups."""
    return build_plan(
        [device_input(d) for d in household.devices],
        [backup_input(b) for b in household.backups],
        household_facts(household),
        now=now,
        saver_preview=saver_preview,
    )


# --- Changes -----------------------------------------------------------------------------


class HouseholdService:
    """Changes to a household and everything in it, for one request."""

    def __init__(
        self, session: AsyncSession, broker: EventBroker, geocoder: Geocoder, clock: Clock
    ) -> None:
        self._session = session
        self._broker = broker
        self._geocoder = geocoder
        self._clock = clock

    async def create(self, body: schemas.HouseholdCreate) -> tuple[models.Household, str]:
        """Create a household. Returns it and the owner token (the only time it is shown)."""
        lat, lon, found_state = await self._locate(
            body.address, body.lat, body.lon, need_state=body.state is None
        )
        token = new_token("owner")
        household = models.Household(
            id=new_id("hh"),
            name=body.name,
            patient_first_name=body.patient_first_name,
            address=body.address or None,
            lat=lat,
            lon=lon,
            state=body.state or found_state,
            utility_name=body.utility_name or None,
            medical_program_registered=False,
            join_code=await self._unused_join_code(),
            armed=True,
            owner_token_hash=hash_token(token),
            created_at=self._clock(),
            devices=[],
            backups=[],
            caregivers=[],
        )
        self._session.add(household)
        await self._session.commit()
        return household, token

    async def update(
        self, household: models.Household, body: schemas.HouseholdUpdate
    ) -> schemas.Household:
        """Apply the fields the client sent, keeping the location consistent with the address.

        A new address without new coordinates is looked up, and the old coordinates are dropped
        even if the lookup fails, since they belonged to the old address. New coordinates
        without a new state get their state looked up. Coordinates and state equal to the saved
        ones count as not sent: a client may send back the whole household it received with
        only the address edited. Returns the household as saved (and announced).
        """
        changes = body.model_dump(exclude_unset=True)
        for name in ("address", "utility_name"):
            if name in changes:
                changes[name] = changes[name] or None
        if "lat" in changes and (changes["lat"], changes["lon"]) == (household.lat, household.lon):
            del changes["lat"], changes["lon"]
        if "state" in changes and changes["state"] == household.state:
            del changes["state"]
        address_changed = "address" in changes and changes["address"] != household.address

        # Look up first and change afterwards: a lookup can take seconds, and nothing has been
        # written (so no other request waits on this one) in the meantime.
        need_state = "state" not in changes
        found_state = None
        if "lat" in changes:
            _, _, found_state = await self._locate(
                None, changes["lat"], changes["lon"], need_state=need_state
            )
        elif address_changed and changes["address"]:
            changes["lat"], changes["lon"], found_state = await self._locate(
                changes["address"], None, None, need_state=need_state
            )
        if found_state and need_state:
            changes["state"] = found_state

        for name, value in changes.items():
            setattr(household, name, value)
        return await self._save(household)

    async def rotate_join_code(self, household: models.Household) -> str:
        """Give the household a new join code; the old one stops working."""
        household.join_code = await self._unused_join_code()
        await self._save(household)
        return household.join_code

    async def delete(self, household: models.Household) -> None:
        """Delete a household and everything in it, and close its live streams."""
        await self._session.delete(household)
        await self._session.commit()
        self._broker.close(household.id)

    async def add_device(
        self, household: models.Household, body: schemas.DeviceCreate
    ) -> models.Device:
        """Add a device, filling unspecified fields from the catalog (409 beyond the limit)."""
        device = equipment.new_device(body, self._clock())
        household.devices.append(device)
        await self._write(household)
        await self._within_limit(len(household.devices), MAX_DEVICES, "devices")
        await self._commit_and_announce(household)
        return device

    async def update_device(
        self, household: models.Household, device_id: str, body: schemas.DeviceUpdate
    ) -> models.Device:
        """Change a device's fields."""
        device = _find(household.devices, device_id, "Device")
        equipment.update_device(device, body)
        await self._save(household)
        return device

    async def remove_device(self, household: models.Household, device_id: str) -> None:
        """Remove a device."""
        household.devices.remove(_find(household.devices, device_id, "Device"))
        await self._save(household)

    async def add_backup(
        self, household: models.Household, body: schemas.BackupCreate
    ) -> models.Backup:
        """Add a backup power source, filling unspecified fields from the catalog.

        409 beyond the limit, which also keeps the planner's work small (see `MAX_BACKUPS`).
        """
        backup = equipment.new_backup(body, self._clock())
        household.backups.append(backup)
        await self._write(household)
        await self._within_limit(len(household.backups), MAX_BACKUPS, "backup power sources")
        await self._commit_and_announce(household)
        return backup

    async def update_backup(
        self, household: models.Household, backup_id: str, body: schemas.BackupUpdate
    ) -> models.Backup:
        """Change a backup's fields."""
        backup = _find(household.backups, backup_id, "Backup")
        equipment.update_backup(backup, body)
        await self._save(household)
        return backup

    async def remove_backup(self, household: models.Household, backup_id: str) -> None:
        """Remove a backup power source."""
        household.backups.remove(_find(household.backups, backup_id, "Backup"))
        await self._save(household)

    async def join(
        self, body: schemas.JoinRequest
    ) -> tuple[models.Caregiver, str, schemas.Household]:
        """Join a household as a caregiver with its join code.

        Returns the caregiver, their token (the only time it is shown) and the household.
        """
        code = normalize_join_code(body.join_code)
        household = await self._session.scalar(
            select(models.Household).where(models.Household.join_code == code)
        )
        if household is None:
            raise not_found(_UNKNOWN_JOIN_CODE)
        token = new_token("caregiver")
        caregiver = models.Caregiver(
            id=new_id("cg"),
            name=body.name,
            relationship=body.relationship or None,
            token_hash=hash_token(token),
            joined_at=self._clock(),
        )
        household.caregivers.append(caregiver)
        await self._write(household)
        if household.join_code != code:
            # The owner replaced the code (for example by removing a caregiver) while this
            # request was running; the old code must not let anyone in.
            await self._session.rollback()
            raise not_found(_UNKNOWN_JOIN_CODE)
        await self._within_limit(len(household.caregivers), MAX_CAREGIVERS, "caregivers")
        return caregiver, token, await self._commit_and_announce(household)

    async def remove_caregiver(self, household: models.Household, caregiver_id: str) -> None:
        """Remove a caregiver and revoke their access.

        Their token stops working and their live streams close. They have seen the join code,
        so it is replaced too; otherwise they could simply join again. The caregivers who
        remain see the new code.
        """
        household.caregivers.remove(_find(household.caregivers, caregiver_id, "Caregiver"))
        household.join_code = await self._unused_join_code()
        await self._write(household)
        await self._commit_and_announce(household, revoked_viewer=caregiver_id)

    # --- Writing -------------------------------------------------------------------------

    async def _save(self, household: models.Household) -> schemas.Household:
        """Write, commit and announce a change that has no limit to check."""
        await self._write(household)
        return await self._commit_and_announce(household)

    async def _write(self, household: models.Household) -> None:
        """Write this request's changes, then read the household again in the same transaction.

        Writing takes SQLite's single write lock until the commit (see the module docstring), so
        the household read here is current, and stays current until this request commits.
        """
        await self._session.flush()
        current = await self._session.scalar(
            select(models.Household)
            .where(models.Household.id == household.id)
            .execution_options(populate_existing=True)
        )
        if current is None:  # deleted by its owner a moment ago
            await self._session.rollback()
            raise not_found("Household not found.")

    async def _within_limit(self, count: int, maximum: int, what: str) -> None:
        """409, undoing this request's change, if a household now has more than `maximum`."""
        if count > maximum:
            await self._session.rollback()
            raise conflict(f"A household can have at most {maximum} {what}.")

    async def _commit_and_announce(
        self, household: models.Household, *, revoked_viewer: str | None = None
    ) -> schemas.Household:
        """Commit, then tell everyone watching the household. Returns what was announced.

        `revoked_viewer` is a caregiver who was just removed: their streams are closed before
        the announcement goes out, so they never receive the household as it now is.
        """
        view = household_view(household)
        await self._session.commit()
        if revoked_viewer is not None:
            self._broker.close(household.id, viewer_id=revoked_viewer)
        self._broker.publish(household.id, "household_updated", HouseholdData(household=view))
        return view

    async def _unused_join_code(self) -> str:
        """A join code no other household has (31^6, about 900 million, are possible)."""
        for _ in range(_JOIN_CODE_ATTEMPTS):
            code = new_join_code()
            taken = await self._session.scalar(
                select(exists().where(models.Household.join_code == code))
            )
            if not taken:
                return code
        raise RuntimeError("Couldn't find an unused join code")

    async def _locate(
        self, address: str | None, lat: float | None, lon: float | None, *, need_state: bool
    ) -> tuple[float | None, float | None, str | None]:
        """Best-effort location: (lat, lon, state found).

        With coordinates, look up their state if `need_state`; otherwise look up the address's
        coordinates and state. Gives up quietly after a few seconds: a missing location only
        means the plan suggests adding one.
        """
        found_state = None
        try:
            async with asyncio.timeout(LOCATION_LOOKUP_BUDGET_SECONDS):
                if lat is not None and lon is not None:
                    if need_state:
                        found_state = await self._geocoder.state_for(lat, lon)
                elif address:
                    result = await self._geocoder.geocode(address)
                    if result is not None:
                        lat, lon, found_state = result.lat, result.lon, result.state
        except TimeoutError:
            logger.warning("Location lookup took too long; continuing without it")
        return lat, lon, found_state


def _find[T: (models.Device, models.Backup, models.Caregiver)](
    items: list[T], item_id: str, what: str
) -> T:
    """The item with this id in one of a household's lists (404 if it isn't there)."""
    for item in items:
        if item.id == item_id:
            return item
    raise not_found(f"{what} not found.")
