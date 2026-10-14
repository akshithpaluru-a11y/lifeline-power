"""Outage timeline: milestones, check-ins and drills. Pure functions, no I/O.

Times come in two flavours here:

* **outage minutes**: how far into the outage the batteries are. Plan runtimes are in these.
* **real time**: wall-clock datetimes such as `Milestone.at`.

For real outages they are the same. A drill with `speed` 300 runs the outage 300 times faster:
one real second is five outage minutes, so a 6-hour countdown takes 72 seconds. Every
conversion goes through `to_real_time` / `outage_minutes_between`, so nothing else needs to know
about `speed`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from app import help_sites
from app.planner import (
    INVERTER_EFFICIENCY,
    USABLE_BATTERY_FRACTION,
    BackupInput,
    DeviceInput,
    effective_draw_watts,
    incompatibility,
    own_battery_wh,
    running_watts,
)
from app.schemas import (
    DevicePlan,
    HelpSite,
    Milestone,
    MilestoneKey,
    OutageSource,
    Plan,
    Severity,
)
from app.timeutil import format_duration, minutes

DEFAULT_TRAVEL_MINUTES = 30.0
"""Travel time assumed when we don't know where the nearest help site is."""
LEAVE_BUFFER_MINUTES = 30.0
"""Safety margin: arrive at the powered location with this much battery left."""
CRITICAL_LOW_MINUTES = 30.0
CHECK_IN_INTERVAL_MINUTES = 60.0
MISSED_CHECK_IN_GRACE_MINUTES = 15.0
"""A missed check-in is reported this long after it was due, then again every 15 minutes."""
MAX_TIMELINE_MINUTES = 10 * 365 * 24 * 60.0
"""Milestones are never placed more than about ten years out.

Only implausible figures (a fraction of a watt on huge batteries) give runtimes that long, and
`datetime` stops at the year 9999, so without this cap such a plan would crash the outage.
"""
EMERGENCY_BACKUP = (
    "your emergency backup, such as oxygen cylinders or a manual resuscitation bag if you have them"
)
"""What to use while a life-sustaining device has no power (in the alerts that say so)."""

_START_KEYS: frozenset[str] = frozenset({"power_lost", "conserve"})
_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class HelpTarget:
    """Where the family would go, and how long it takes to get there (outage minutes).

    `name` is None when we don't know a place likely to have power; `travel_minutes` is then
    the drive we allow for.
    """

    name: str | None
    travel_minutes: float

    @classmethod
    def from_sites(
        cls, sites: Sequence[HelpSite], searched_radius_km: float | None = None
    ) -> "HelpTarget":
        """The nearest site likely to have power (hospital, fire or police station).

        Libraries, community centres and shelters are never chosen: in a widespread outage
        they are usually dark too, so timing `leave_by` on the library down the road would
        send a family somewhere without power with no time left to go further.

        `searched_radius_km` is how far the lookup that found `sites` reached (None when there
        was no lookup, or the map service didn't answer). If nothing with power turned up
        within it, the nearest such place is at least that far away, so we allow the drive to
        the edge of the search area, never less than the 30-minute default.
        """
        powered = [site for site in sites if site.likely_has_power]
        if powered:
            nearest = min(powered, key=lambda site: site.travel_minutes)
            return cls(name=nearest.name, travel_minutes=float(nearest.travel_minutes))
        travel = DEFAULT_TRAVEL_MINUTES
        if searched_radius_km is not None:
            travel = max(travel, float(help_sites.travel_minutes(searched_radius_km)))
        return cls(name=None, travel_minutes=travel)


@dataclass(frozen=True, slots=True)
class TimelineContext:
    """Everything besides the plan that shapes an outage's milestones."""

    source: OutageSource
    speed: float
    started_at: datetime
    plan_at: datetime
    """When the plan was computed: the outage start, or the latest check-in."""
    last_check_in_at: datetime
    help_target: HelpTarget
    device_labels: Mapping[str, str]


def to_real_time(origin: datetime, outage_minutes: float, speed: float) -> datetime:
    """The wall-clock time `outage_minutes` after `origin`, at the given drill speed.

    Capped at `MAX_TIMELINE_MINUTES`, so absurdly long runtimes can't overflow `datetime`.
    """
    return origin + minutes(min(outage_minutes, MAX_TIMELINE_MINUTES) / speed)


def outage_minutes_between(start: datetime, end: datetime, speed: float) -> float:
    """How many outage minutes pass between two wall-clock times."""
    return (end - start).total_seconds() / 60 * speed


def build_milestones(
    plan: Plan, ctx: TimelineContext, previous: Sequence[Milestone] | None = None
) -> list[Milestone]:
    """Lay out the outage timeline from a plan.

    `previous` is the timeline before a check-in. A milestone that was already announced stays
    `passed` if its new time is still in the past; anything else starts un-passed so the ticker
    announces it when its time comes (even if that is right away). On a brand-new outage the
    start milestones are passed immediately, because `outage_started` announces them; a rebuild
    keeps them exactly as they were announced, since they describe the moment the power went out.
    """
    announced = {m.key: m for m in previous or () if m.key in _START_KEYS}
    start_milestones = [
        announced.get(m.key, m.model_copy(update={"passed": True}))
        for m in (
            _milestone(
                "power_lost",
                ctx.started_at,
                _power_lost_title(ctx.source),
                _power_lost_detail(plan, ctx),
                "info",
            ),
            _milestone(
                "conserve", ctx.started_at, "Save power now", _conserve_detail(plan), "info"
            ),
        )
    ]

    was_passed = {m.key: m.passed for m in previous or ()}
    timeline = [
        _milestone(
            "check_in",
            to_real_time(ctx.last_check_in_at, CHECK_IN_INTERVAL_MINUTES, ctx.speed),
            "Time to check in",
            'Tap "I\'m OK" and update battery levels so your caregivers know you are safe.',
            "info",
        ),
    ]
    if plan.critical_runtime_minutes is not None:
        timeline += _countdown_milestones(
            plan,
            ctx,
            plan.critical_runtime_minutes,
            halfway_announced=was_passed.get("halfway", False),
        )

    timeline = start_milestones + [
        item.model_copy(
            update={"passed": was_passed.get(item.key, False) and item.at <= ctx.plan_at}
        )
        for item in timeline
    ]
    timeline.sort(key=lambda m: m.at)
    return timeline


def _countdown_milestones(
    plan: Plan, ctx: TimelineContext, remaining: float, *, halfway_announced: bool
) -> list[Milestone]:
    """Milestones that count down the critical runtime (`remaining` outage minutes from now).

    `halfway_announced` says whether the ticker already sent the halfway alert, which then
    stays on the timeline as a record even when it would no longer be scheduled.
    """
    device = _bottleneck_label(plan, ctx)
    elapsed = outage_minutes_between(ctx.started_at, ctx.plan_at, ctx.speed)
    total = elapsed + remaining
    travel = ctx.help_target.travel_minutes
    # Outage minutes from now until each warning; negative means it is already overdue.
    halfway_in = total / 2 - elapsed
    leave_in = remaining - travel - LEAVE_BUFFER_MINUTES

    def at(outage_minutes_from_plan: float) -> datetime:
        return to_real_time(ctx.plan_at, max(0.0, outage_minutes_from_plan), ctx.speed)

    milestones: list[Milestone] = []
    # Halfway says "get ready to leave", so it only belongs before leave_by: with a short
    # runtime it would otherwise arrive after "Leave now" and even after "Call 911", and with
    # no power at all it would say "About 0 min left... get ready". One that was already
    # announced and is still in the past stays on the timeline as a record.
    if max(0.0, halfway_in) < max(0.0, leave_in) or (halfway_announced and halfway_in <= 0):
        # A check-in that finds less battery than planned can put halfway in the past. It is
        # then announced right away, and what is left now is all that is left (not total / 2).
        left_at_halfway = remaining - max(0.0, halfway_in)
        milestones.append(
            _milestone(
                "halfway",
                at(halfway_in),
                "Halfway through battery power",
                f"About {format_duration(left_at_halfway)} left for {device}. Get ready to leave "
                "if the power isn't back.",
                "warning",
            )
        )

    milestones.append(_leave_by(ctx, device, remaining, at(leave_in), leave_in > 0))
    if remaining <= travel:
        critical_low_detail = (
            f"{device} is close to running out and there isn't enough battery to reach power. "
            "Call 911 now."
        )
    else:
        critical_low_detail = (
            f"{device} is close to running out (about "
            f"{format_duration(min(remaining, CRITICAL_LOW_MINUTES))} left). Call 911 now unless "
            "you will reach power before then."
        )
    milestones.append(
        _milestone(
            "critical_low",
            at(remaining - CRITICAL_LOW_MINUTES),
            "30 minutes of power left"
            if remaining >= CRITICAL_LOW_MINUTES
            else "Less than 30 minutes of power left",
            critical_low_detail,
            "critical",
        )
    )

    if remaining <= 0:
        depleted_detail = (
            f"{device} has no battery or backup power. If you can't get power right away, call 911."
        )
    else:
        depleted_detail = (
            f"{device} is estimated to run out of power now. If you haven't reached power, "
            "call 911."
        )
    milestones.append(
        _milestone("depleted", at(remaining), "Batteries may be empty", depleted_detail, "critical")
    )
    return milestones


def _leave_by(
    ctx: TimelineContext, device: str, remaining: float, when: datetime, ahead: bool
) -> Milestone:
    """When to set off for the help site, or a 911 alert if the battery can't last the drive.

    `ahead` is False when it's already time to leave (the alert then says "now").
    """
    travel = format_duration(ctx.help_target.travel_minutes)
    name = ctx.help_target.name
    if remaining <= ctx.help_target.travel_minutes:
        # Setting off would leave the device to run out on the road with no help coming.
        destination = (
            f"{name} ({travel} away)"
            if name
            else f"the nearest place with power (allowing {travel} to get there)"
        )
        situation = (
            f"{device} has no battery or backup power to last the trip to {destination}."
            if remaining <= 0
            else f"{device} has about {format_duration(remaining)} of power left, not enough to "
            f"reach {destination}."
        )
        return _milestone(
            "leave_by",
            when,
            "Not enough battery to reach power",
            f"{situation} Call 911 now. While you wait, use {EMERGENCY_BACKUP}.",
            "critical",
        )
    if name:
        detail = f"Head to {name} ({travel} away) before batteries get low."
    else:
        detail = (
            "Head to a hospital, fire station or other place with power before batteries get "
            f"low. This allows {travel} to get there; leave earlier if the nearest one is farther."
        )
    return _milestone(
        "leave_by",
        when,
        "Leave for a powered location" if ahead else "Leave for a powered location now",
        detail,
        "warning",
    )


def _bottleneck_label(plan: Plan, ctx: TimelineContext) -> str:
    """The name of the life-sustaining device that runs out first."""
    return ctx.device_labels.get(plan.bottleneck_device_id or "", "life-sustaining devices")


def _milestone(
    key: MilestoneKey, when: datetime, title: str, detail: str, severity: Severity
) -> Milestone:
    return Milestone(key=key, at=when, title=title, detail=detail, severity=severity, passed=False)


def _power_lost_title(source: OutageSource) -> str:
    return {
        "charging_lost": "Power went out",
        "manual": "Outage reported",
        "drill": "Drill: the power is out",
    }[source]


def _power_lost_detail(plan: Plan, ctx: TimelineContext) -> str:
    """The first thing the family reads: say so plainly if a life-sustaining device has stopped."""
    critical = plan.critical_runtime_minutes
    if critical is not None and critical <= 0:
        return (
            f"{_bottleneck_label(plan, ctx)} has no battery or backup power, so it stopped when "
            f"the power went out. Switch now to {EMERGENCY_BACKUP}, and call 911 if you can't get "
            "power right away."
        )
    return "Devices are running on batteries and backup power now."


def _conserve_detail(plan: Plan) -> str:
    """Advice for the first minutes of an outage, including the plan's saver-mode tips.

    Saver tips change how a medical device runs (for an oxygen concentrator, the dose), so they
    always come after the care-team caveat, never as a bare instruction.
    """
    tips = [r.title for r in plan.recommendations if r.kind == "saver_mode"]
    detail = "Unplug everything that isn't medical and keep the fridge closed."
    if tips:
        detail += (
            " Lower-power settings can make batteries last longer. Only if your care team has "
            "said it is safe: " + "; ".join(tips) + "."
        )
    return detail


def mark_due(
    milestones: Sequence[Milestone], now: datetime
) -> tuple[list[Milestone], list[Milestone]]:
    """Mark milestones whose time has come as passed.

    Returns the updated timeline and the milestones that have just passed (to announce).
    """
    updated: list[Milestone] = []
    newly_passed: list[Milestone] = []
    for item in milestones:
        if item.passed or item.at > now:
            updated.append(item)
            continue
        done = item.model_copy(update={"passed": True})
        updated.append(done)
        newly_passed.append(done)
    return updated, newly_passed


def missed_check_in_minutes(
    milestones: Sequence[Milestone],
    last_check_in_at: datetime,
    speed: float,
    now: datetime,
    alerts_sent: int,
) -> float | None:
    """Outage minutes the owner is overdue, if it's time for another missed-check-in alert.

    The first alert goes out 15 minutes after the check-in was due, then one every 15 minutes
    after that, until the owner checks in.
    """
    check_in = next((m for m in milestones if m.key == "check_in"), None)
    if check_in is None or last_check_in_at >= check_in.at:
        return None
    overdue = outage_minutes_between(check_in.at, now, speed)
    if overdue >= MISSED_CHECK_IN_GRACE_MINUTES * (alerts_sent + 1):
        return overdue
    return None


def missed_check_in_alerts_covered(overdue_minutes: float) -> int:
    """How many 15-minute intervals an alert sent `overdue_minutes` late accounts for.

    Recording this (rather than adding one per alert) means that after a gap in ticking, such
    as a server restart, caregivers get one alert with the real figure instead of a burst of
    catch-up alerts, one every tick, until the count catches up.
    """
    return int(overdue_minutes // MISSED_CHECK_IN_GRACE_MINUTES)


# --- Check-ins: estimate where the batteries are now ------------------------------------


@dataclass(frozen=True, slots=True)
class BackupLevel:
    """A reported backup level at check-in."""

    charge_percent: float | None = None
    fuel_hours: float | None = None


@dataclass(frozen=True, slots=True)
class _PluggedIn:
    """A device whose battery reads fuller than estimated: it ran on something else."""

    device: DeviceInput
    """The device as it was when the plan was made."""
    plan: DevicePlan
    reading: float
    """Internal battery charge the owner reported, in percent."""
    used_wh: float
    """Energy the device used (at the device) since the plan, after any generator time."""


def advance_state(
    devices: Sequence[DeviceInput],
    backups: Sequence[BackupInput],
    plan: Plan,
    elapsed_minutes: float,
    reported_charge: Mapping[str, float] | None = None,
) -> tuple[list[DeviceInput], list[BackupInput]]:
    """Estimate every battery's level after `elapsed_minutes` of the outage under `plan`.

    The planner only decides *how much* energy each device gets. To estimate levels part-way
    through we also need an order, and we use the one that makes the plan's sums work:

    1. Generators and car inverters first, all at once (a generator runs everything plugged
       into it at the same time, so its fuel hours count once for all of them).
    2. Then each device's own battery: internal first, then spares.
    3. Then its share of the shared batteries, drawn proportionally.

    Re-planning from the estimated state gives (up to rounding) the old runtime minus the
    elapsed time, so a check-in with no new readings leaves the countdown where it was. The
    drain uses each device's exact average draw, not the plan's figures rounded for display, so
    low-power devices such as feeding pumps keep counting down correctly too.

    `reported_charge` holds the internal battery levels the owner read off devices at this
    check-in. A reading *higher* than step 2's estimate means the device really ran on
    something else, usually a power station or UPS: an AC device plugged into one runs from the
    outlet and keeps its own battery full. The energy its battery didn't supply (plus any charge
    it gained) is then taken from the shared batteries that can run it (its plan's allocations
    first, then any other), and only after those from its spares. Without this a truthful
    "100%" would hide the station's drain and the countdown would never go down. A reading at
    or below the estimate changes nothing here (the caller applies it): a battery that drained
    faster than expected says nothing about the shared ones, and handing their energy back would
    be guessing in the unsafe direction.
    """
    reported_charge = reported_charge or {}
    by_id = {p.device_id: p for p in plan.devices}
    fuel_ids = {b.id for b in backups if b.is_fuel}
    batteries = [b for b in backups if not b.is_fuel]
    # Energy still in each shared battery (at the battery, before any inverter loss).
    left_wh = {b.id: b.usable_wh for b in batteries}
    fuel_used_minutes: dict[str, float] = {}
    estimated: dict[str, DeviceInput] = {}
    plugged_in: list[_PluggedIn] = []

    for device in devices:
        entry = by_id.get(device.id)
        if entry is None:
            continue  # added since the plan was made, so nothing has been drawn for it yet
        minutes_left = max(0.0, elapsed_minutes)
        for allocation in entry.allocations:
            if allocation.backup_id in fuel_ids:
                used = min(minutes_left, allocation.minutes)
                previous = fuel_used_minutes.get(allocation.backup_id, 0.0)
                fuel_used_minutes[allocation.backup_id] = max(previous, used)
                minutes_left -= used

        used_wh = minutes_left * effective_draw_watts(device) / 60
        from_own = min(used_wh, own_battery_wh(device))
        drained = _drain_own_batteries(device, from_own)
        reading = reported_charge.get(device.id)
        # A reading above the estimate: it ran on something else (see above). Not if a
        # generator ran it the whole time, though: the generator charged it as well.
        if (
            reading is not None
            and device.internal_battery_wh > 0
            and minutes_left > 0
            and reading > drained.charge_percent + _TOLERANCE
        ):
            plugged_in.append(_PluggedIn(device, entry, reading, used_wh))
            continue
        estimated[device.id] = drained
        allocations = _battery_allocations(entry, fuel_ids)
        to_draw = (used_wh - from_own) / _inverter_efficiency(device)
        _take_proportionally(left_wh, allocations, min(to_draw, sum(allocations.values())))

    # After every device that followed the plan, so their planned draws are counted first.
    for item in plugged_in:
        device = item.device
        full_wh = device.internal_battery_wh * USABLE_BATTERY_FRACTION
        from_internal = (device.charge_percent - item.reading) / 100 * full_wh  # < 0: it charged
        efficiency = _inverter_efficiency(device)
        to_draw = (item.used_wh - from_internal) / efficiency
        allocations = _battery_allocations(item.plan, fuel_ids)
        to_draw = _take_proportionally(left_wh, allocations, to_draw)
        others = {
            b.id: left_wh[b.id]
            for b in batteries
            if b.id not in allocations and incompatibility(b, device, running_watts(device)) is None
        }
        to_draw = _take_proportionally(left_wh, others, to_draw)
        estimated[device.id] = _drain_spares(
            replace(device, charge_percent=item.reading), to_draw * efficiency
        )

    new_backups = []
    for backup in backups:
        if backup.is_fuel:
            used_hours = fuel_used_minutes.get(backup.id, 0.0) / 60
            new_backups.append(
                replace(backup, fuel_hours=max(0.0, (backup.fuel_hours or 0.0) - used_hours))
            )
        elif backup.capacity_wh:
            charge = 100 * left_wh[backup.id] / backup.capacity_wh
            new_backups.append(replace(backup, charge_percent=charge))
        else:
            new_backups.append(backup)
    return [estimated.get(d.id, d) for d in devices], new_backups


def _inverter_efficiency(device: DeviceInput) -> float:
    """Share of a shared battery's energy that reaches the device (AC devices lose 15%)."""
    return INVERTER_EFFICIENCY if device.ac_powered else 1.0


def _battery_allocations(entry: DevicePlan, fuel_ids: set[str]) -> dict[str, float]:
    """Battery backup id -> energy (Wh at the battery) the plan gives this device."""
    return {a.backup_id: a.energy_wh for a in entry.allocations if a.backup_id not in fuel_ids}


def _take_proportionally(
    left_wh: dict[str, float], weights: Mapping[str, float], wanted_wh: float
) -> float:
    """Draw `wanted_wh` from the batteries in `weights`, split in proportion to the weights.

    No battery gives more than it has left (`left_wh`, updated in place): its unmet share is
    spread over the others. Returns the energy none of them could supply.
    """
    pool = {b: w for b, w in weights.items() if w > 0 and left_wh.get(b, 0.0) > 0}
    weight_left = sum(pool.values())
    # Batteries that would run dry first go first, so their shortfall passes to the rest.
    for backup_id in sorted(pool, key=lambda b: left_wh[b] / pool[b]):
        take = min(left_wh[backup_id], wanted_wh * pool[backup_id] / weight_left)
        left_wh[backup_id] -= take
        wanted_wh -= take
        weight_left -= pool[backup_id]
    return max(0.0, wanted_wh)


def _drain_own_batteries(device: DeviceInput, used_wh: float) -> DeviceInput:
    """Take `used_wh` of usable energy from the internal battery, then from spares."""
    internal_usable = device.internal_battery_wh * device.charge_percent / 100
    internal_usable *= USABLE_BATTERY_FRACTION
    from_internal = min(used_wh, internal_usable)
    charge = device.charge_percent
    if device.internal_battery_wh > 0:
        full_usable = device.internal_battery_wh * USABLE_BATTERY_FRACTION
        charge = 100 * (internal_usable - from_internal) / full_usable
    return _drain_spares(replace(device, charge_percent=max(0.0, charge)), used_wh - from_internal)


def _drain_spares(device: DeviceInput, used_wh: float) -> DeviceInput:
    """Take `used_wh` of usable energy from the device's spare batteries."""
    spares = max(0.0, device.spare_batteries_wh - used_wh / USABLE_BATTERY_FRACTION)
    return replace(device, spare_batteries_wh=spares)


def apply_reports(
    devices: Sequence[DeviceInput],
    backups: Sequence[BackupInput],
    device_charge: Mapping[str, float],
    backup_levels: Mapping[str, BackupLevel],
) -> tuple[list[DeviceInput], list[BackupInput]]:
    """Overwrite estimated levels with what the owner actually reported at check-in."""
    new_devices = [
        replace(d, charge_percent=device_charge[d.id]) if d.id in device_charge else d
        for d in devices
    ]
    new_backups = []
    for backup in backups:
        level = backup_levels.get(backup.id)
        if level is not None and level.charge_percent is not None and not backup.is_fuel:
            backup = replace(backup, charge_percent=level.charge_percent)
        if level is not None and level.fuel_hours is not None and backup.is_fuel:
            backup = replace(backup, fuel_hours=level.fuel_hours)
        new_backups.append(backup)
    return new_devices, new_backups


def merge_with_household(
    state_devices: Sequence[DeviceInput],
    state_backups: Sequence[BackupInput],
    household_devices: Sequence[DeviceInput],
    household_backups: Sequence[BackupInput],
) -> tuple[list[DeviceInput], list[BackupInput]]:
    """Combine the outage's battery levels with the household's current device settings.

    Settings the family changed during the outage (such as switching on saver mode) come from
    the household; battery levels come from the outage's running estimate. Devices or backups
    added mid-outage join with their household values; removed ones drop out.
    """
    device_levels = {d.id: d for d in state_devices}
    backup_levels = {b.id: b for b in state_backups}
    devices = [
        replace(
            d,
            charge_percent=device_levels[d.id].charge_percent,
            # Spares can only have been used up, unless the family removed some meanwhile.
            spare_batteries_wh=min(d.spare_batteries_wh, device_levels[d.id].spare_batteries_wh),
        )
        if d.id in device_levels
        else d
        for d in household_devices
    ]
    backups = [
        replace(
            b,
            charge_percent=backup_levels[b.id].charge_percent,
            fuel_hours=backup_levels[b.id].fuel_hours,
        )
        if b.id in backup_levels and backup_levels[b.id].type == b.type
        else b
        for b in household_backups
    ]
    return devices, backups
