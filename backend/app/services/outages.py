"""Outages: starting one, check-ins, caregiver acknowledgements and ending it.

An outage keeps three snapshots as JSON: the plan, the milestone timeline, and the battery
levels the plan was computed from (`energy_state`). A check-in estimates where the batteries
are now, applies anything the owner reported, and recomputes all three.

Requests and the background ticker both change outages. Every change happens while holding the
household's lock in `OutageLocks`, so they never overwrite each other. (The row's version
counter is a second line of defence; it would catch a clash if the app ever ran as several
processes.)
"""

import asyncio
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app import models, schemas
from app.auth import Principal
from app.errors import bad_request, conflict, not_found
from app.events import AckData, CheckInData, EventBroker, OutageEventData
from app.help_sites import HelpSiteFinder
from app.ids import new_id
from app.outage_engine import (
    BackupLevel,
    HelpTarget,
    TimelineContext,
    advance_state,
    apply_reports,
    build_milestones,
    merge_with_household,
    outage_minutes_between,
    replug_detail,
)
from app.planner import BackupInput, DeviceInput, build_plan, plugs_of
from app.services.households import backup_input, device_input, household_facts
from app.timeutil import Clock

HELP_LOOKUP_BUDGET_SECONDS = 3.0
"""Longest starting an outage waits to find the nearest help site (otherwise: 30 min travel)."""
HELP_SEARCH_RADII_KM = (15.0, 50.0)
"""Where to look for a place with power: nearby first, then (rural homes) further out."""
HISTORY_LIMIT = 200
MAX_ACKS_PER_CAREGIVER = 5
"""Only each caregiver's most recent acknowledgements are kept on an outage.

The outage, acks included, goes out with every event and response, so a retrying or misbehaving
client must not be able to grow it without bound. A caregiver's latest message and arrival time
are what the family needs.
"""


class OutageLocks:
    """One lock per household, held while its outage is read and changed."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def __call__(self, household_id: str) -> asyncio.Lock:
        """The household's lock, for `async with locks(household_id):`."""
        return self._locks.setdefault(household_id, asyncio.Lock())


def outage_view(outage: models.Outage) -> schemas.Outage:
    """The API representation of an outage."""
    return schemas.Outage.model_validate(outage)


def dump_milestones(milestones: list[schemas.Milestone]) -> list[Any]:
    """Milestones as JSON for storage."""
    return [m.model_dump(mode="json") for m in milestones]


def load_milestones(outage: models.Outage) -> list[schemas.Milestone]:
    """An outage's stored milestones."""
    return [schemas.Milestone.model_validate(m) for m in outage.milestones]


def _energy_state(devices: list[DeviceInput], backups: list[BackupInput]) -> dict[str, Any]:
    return {"devices": [asdict(d) for d in devices], "backups": [asdict(b) for b in backups]}


def _load_energy_state(state: dict[str, Any]) -> tuple[list[DeviceInput], list[BackupInput]]:
    return (
        [DeviceInput(**d) for d in state["devices"]],
        [BackupInput(**b) for b in state["backups"]],
    )


def _labels(household: models.Household) -> dict[str, str]:
    return {device.id: device.label for device in household.devices}


def _kinds(household: models.Household) -> dict[str, str]:
    return {device.id: device.kind for device in household.devices}


def _life_sustaining(household: models.Household) -> frozenset[str]:
    return frozenset(device.id for device in household.devices if device.life_sustaining)


def _backup_labels(household: models.Household) -> dict[str, str]:
    return {backup.id: backup.label for backup in household.backups}


async def active_outage(session: AsyncSession, household_id: str) -> models.Outage | None:
    """The household's active outage, freshly read from the database."""
    return await session.scalar(
        select(models.Outage)
        .where(models.Outage.household_id == household_id, models.Outage.status == "active")
        .execution_options(populate_existing=True)
    )


class OutageService:
    """Outage operations for one request."""

    def __init__(
        self,
        session: AsyncSession,
        broker: EventBroker,
        locks: OutageLocks,
        help_sites: HelpSiteFinder,
        clock: Clock,
    ) -> None:
        self._session = session
        self._broker = broker
        self._locks = locks
        self._help_sites = help_sites
        self._clock = clock

    async def current(self, household_id: str) -> schemas.Outage | None:
        """The active outage, or None."""
        outage = await active_outage(self._session, household_id)
        return None if outage is None else outage_view(outage)

    async def history(self, household_id: str) -> list[schemas.OutageSummary]:
        """Past and current outages, newest first, without their plans."""
        outages = await self._session.scalars(
            select(models.Outage)
            .where(models.Outage.household_id == household_id)
            .order_by(models.Outage.started_at.desc())
            .limit(HISTORY_LIMIT)
            .options(defer(models.Outage.plan), defer(models.Outage.energy_state))
        )
        return [schemas.OutageSummary.model_validate(outage) for outage in outages]

    async def start(
        self, household: models.Household, body: schemas.OutageCreate
    ) -> schemas.Outage:
        """Start an outage (or drill): plan from the saved equipment, then lay out milestones.

        A real outage replaces a drill that is still running. Otherwise only one outage can be
        active at a time.
        """
        if body.source == "charging_lost" and not household.armed:
            raise conflict(
                "The outage alarm is turned off for this home. Turn it on, or report the outage "
                "manually."
            )
        target = await self._help_target(household)
        async with self._locks(household.id):
            ended_drill = await active_outage(self._session, household.id)
            if ended_drill is not None:
                if not ended_drill.drill or body.source == "drill":
                    raise conflict("An outage is already active. End it before starting another.")
                self._finish(ended_drill)
                await self._session.flush()

            now = self._clock()
            devices = [device_input(d) for d in household.devices]
            backups = [backup_input(b) for b in household.backups]
            plan = build_plan(devices, backups, household_facts(household), now=now)
            timeline = TimelineContext(
                source=body.source,
                speed=body.speed,
                started_at=now,
                plan_at=now,
                last_check_in_at=now,
                help_target=target,
                device_labels=_labels(household),
                device_kinds=_kinds(household),
                life_sustaining_ids=_life_sustaining(household),
                backup_labels=_backup_labels(household),
            )
            outage = models.Outage(
                id=new_id("og"),
                household_id=household.id,
                status="active",
                source=body.source,
                speed=body.speed,
                started_at=now,
                ended_at=None,
                last_check_in_at=now,
                last_check_in_ok=True,
                missed_check_in_alerts=0,
                help_site_name=target.name,
                travel_minutes=target.travel_minutes,
                plan=plan.model_dump(mode="json"),
                milestones=dump_milestones(build_milestones(plan, timeline)),
                energy_state=_energy_state(devices, backups),
                acks=[],
            )
            self._session.add(outage)
            try:
                await self._session.commit()
            except IntegrityError as exc:  # another process started one at the same moment
                await self._session.rollback()
                raise conflict("An outage is already active.") from exc

        if ended_drill is not None:
            self._publish_ended(ended_drill)
        view = outage_view(outage)
        self._broker.publish(
            household.id, "outage_started", OutageEventData(outage=view, drill=outage.drill)
        )
        return view

    async def check_in(
        self, household: models.Household, body: schemas.CheckInRequest
    ) -> schemas.Outage:
        """Record that the owner checked in, and recompute the plan and milestones.

        Battery levels are estimated from the time since the last plan, then overwritten with
        anything the owner reported. A device reading fuller than estimated was running on a
        shared battery, which is charged for that energy (see `advance_state`). Device settings
        changed during the outage (such as saver mode) are picked up too.
        """
        device_charge = {r.device_id: r.charge_percent for r in body.devices or ()}
        backup_levels = {
            r.backup_id: BackupLevel(charge_percent=r.charge_percent, fuel_hours=r.fuel_hours)
            for r in body.backups or ()
        }
        _check_ids(device_charge, {d.id for d in household.devices}, "device")
        _check_ids(backup_levels, {b.id for b in household.backups}, "backup")

        async with self._locks(household.id):
            outage = await self._require_active(household.id)
            now = self._clock()
            old_plan = schemas.Plan.model_validate(outage.plan)
            elapsed = outage_minutes_between(old_plan.computed_at, now, outage.speed)
            devices, backups = _load_energy_state(outage.energy_state)
            devices, backups = advance_state(devices, backups, old_plan, elapsed, device_charge)
            devices, backups = merge_with_household(
                devices,
                backups,
                [device_input(d) for d in household.devices],
                [backup_input(b) for b in household.backups],
            )
            devices, backups = apply_reports(devices, backups, device_charge, backup_levels)

            # Keep the plugs where the family put them: a re-plan that quietly moved a device
            # to another backup would push the countdown later without telling anyone.
            plan = build_plan(
                devices,
                backups,
                household_facts(household),
                now=now,
                keep_plugs=plugs_of(old_plan),
            )
            timeline = TimelineContext(
                source=outage.source,
                speed=outage.speed,
                started_at=outage.started_at,
                plan_at=now,
                last_check_in_at=now,
                help_target=HelpTarget(outage.help_site_name, outage.travel_minutes),
                device_labels=_labels(household),
                device_kinds=_kinds(household),
                life_sustaining_ids=_life_sustaining(household),
                backup_labels=_backup_labels(household),
                replug_detail=replug_detail(old_plan, plan, _labels(household)),
            )
            outage.plan = plan.model_dump(mode="json")
            outage.milestones = dump_milestones(
                build_milestones(plan, timeline, previous=load_milestones(outage))
            )
            outage.energy_state = _energy_state(devices, backups)
            outage.last_check_in_at = now
            outage.last_check_in_ok = body.ok
            outage.missed_check_in_alerts = 0
            await self._session.commit()

        view = outage_view(outage)
        self._broker.publish(household.id, "check_in", CheckInData(outage=view))
        return view

    async def ack(
        self, household_id: str, caregiver: Principal, body: schemas.AckRequest
    ) -> schemas.Outage:
        """Record a caregiver's acknowledgement ("On my way").

        Only the caregiver's `MAX_ACKS_PER_CAREGIVER` most recent acknowledgements are kept.
        """
        assert caregiver.caregiver_id is not None and caregiver.caregiver_name is not None
        async with self._locks(household_id):
            outage = await self._require_active(household_id)
            ack = models.Ack(
                caregiver_id=caregiver.caregiver_id,
                name=caregiver.caregiver_name,
                message=body.message or None,
                eta_minutes=body.eta_minutes,
                at=self._clock(),
            )
            earlier = sorted(
                (a for a in outage.acks if a.caregiver_id == caregiver.caregiver_id),
                key=lambda a: (a.at, a.id),
            )
            for old in earlier[: max(0, len(earlier) - MAX_ACKS_PER_CAREGIVER + 1)]:
                outage.acks.remove(old)  # deleted with the commit (delete-orphan)
            outage.acks.append(ack)
            await self._session.commit()

        self._broker.publish(
            household_id,
            "ack",
            AckData(outage_id=outage.id, ack=schemas.Ack.model_validate(ack)),
        )
        return outage_view(outage)

    async def end(self, household_id: str) -> schemas.Outage:
        """End the active outage (the power is back, or the drill is over)."""
        async with self._locks(household_id):
            outage = await self._require_active(household_id)
            self._finish(outage)
            await self._session.commit()
        return self._publish_ended(outage)

    async def _require_active(self, household_id: str) -> models.Outage:
        outage = await active_outage(self._session, household_id)
        if outage is None:
            raise not_found("There is no active outage.")
        return outage

    def _finish(self, outage: models.Outage) -> None:
        outage.status = "ended"
        outage.ended_at = self._clock()

    def _publish_ended(self, outage: models.Outage) -> schemas.Outage:
        view = outage_view(outage)
        self._broker.publish(
            outage.household_id, "outage_ended", OutageEventData(outage=view, drill=outage.drill)
        )
        return view

    async def _help_target(self, household: models.Household) -> HelpTarget:
        """The nearest place likely to have power, if we can find it quickly.

        When OpenStreetMap answers but nothing within 15 km is likely to have power (common in
        rural areas, where the only nearby place may be a library), we look again within 50 km
        so `leave_by` allows for the real drive. If that fails too, the drive is at least the
        distance searched (see `HelpTarget.from_sites`). Both lookups share one time budget.
        """
        if household.lat is None or household.lon is None:
            return HelpTarget.from_sites([])
        sites: list[schemas.HelpSite] = []
        searched_km: float | None = None
        try:
            async with asyncio.timeout(HELP_LOOKUP_BUDGET_SECONDS):
                for radius_km in HELP_SEARCH_RADII_KM:
                    result = await self._help_sites.find(household.lat, household.lon, radius_km)
                    if result.source != "openstreetmap":
                        break  # the map service didn't answer: keep what we already know
                    sites, searched_km = result.sites, radius_km
                    if any(site.likely_has_power for site in sites):
                        break
        except TimeoutError:
            pass  # use whatever the lookups found before time ran out
        return HelpTarget.from_sites(sites, searched_km)


def _check_ids(reported: dict[str, Any], known: set[str], what: str) -> None:
    unknown = sorted(set(reported) - known)
    if unknown:
        raise bad_request(f"Unknown {what} id: {', '.join(unknown)}.")
