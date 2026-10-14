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

from app.planner import USABLE_BATTERY_FRACTION, BackupInput, DeviceInput
from app.schemas import HelpSite, Milestone, MilestoneKey, OutageSource, Plan, Severity
from app.timeutil import format_duration, minutes

DEFAULT_TRAVEL_MINUTES = 30.0
"""Travel time assumed when we don't know where the nearest help site is."""
LEAVE_BUFFER_MINUTES = 30.0
"""Safety margin: arrive at the powered location with this much battery left."""
CRITICAL_LOW_MINUTES = 30.0
CHECK_IN_INTERVAL_MINUTES = 60.0
MISSED_CHECK_IN_GRACE_MINUTES = 15.0
"""A missed check-in is reported this long after it was due, then again every 15 minutes."""

_START_KEYS: frozenset[str] = frozenset({"power_lost", "conserve"})


@dataclass(frozen=True, slots=True)
class HelpTarget:
    """Where the family would go, and how long it takes to get there (outage minutes)."""

    name: str | None
    travel_minutes: float

    @classmethod
    def from_sites(cls, sites: Sequence[HelpSite]) -> "HelpTarget":
        """Pick the best site (sites arrive sorted by usefulness, then distance)."""
        candidates = [site for site in sites if site.likely_has_power] or list(sites)
        if not candidates:
            return cls(name=None, travel_minutes=DEFAULT_TRAVEL_MINUTES)
        return cls(name=candidates[0].name, travel_minutes=float(candidates[0].travel_minutes))


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
    """The wall-clock time `outage_minutes` after `origin`, at the given drill speed."""
    return origin + minutes(outage_minutes / speed)


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
    start milestones are passed immediately, because `outage_started` announces them.
    """
    timeline = [
        _milestone(
            "power_lost",
            ctx.started_at,
            _power_lost_title(ctx.source),
            "Devices are running on batteries and backup power now.",
            "info",
        ),
        _milestone("conserve", ctx.started_at, "Save power now", _conserve_detail(plan), "info"),
        _milestone(
            "check_in",
            to_real_time(ctx.last_check_in_at, CHECK_IN_INTERVAL_MINUTES, ctx.speed),
            "Time to check in",
            'Tap "I\'m OK" and update battery levels so your caregivers know you are safe.',
            "info",
        ),
    ]
    if plan.critical_runtime_minutes is not None:
        timeline += _countdown_milestones(plan, ctx, plan.critical_runtime_minutes)

    was_passed = {m.key: m.passed for m in previous or ()}
    for index, item in enumerate(timeline):
        if previous is None:
            passed = item.key in _START_KEYS
        else:
            passed = was_passed.get(item.key, False) and item.at <= ctx.plan_at
        timeline[index] = item.model_copy(update={"passed": passed})
    timeline.sort(key=lambda m: m.at)
    return timeline


def _countdown_milestones(plan: Plan, ctx: TimelineContext, remaining: float) -> list[Milestone]:
    """Milestones that count down the critical runtime (`remaining` outage minutes from now)."""
    device = ctx.device_labels.get(plan.bottleneck_device_id or "", "life-sustaining devices")
    elapsed = outage_minutes_between(ctx.started_at, ctx.plan_at, ctx.speed)
    total = elapsed + remaining
    travel = ctx.help_target.travel_minutes
    destination = (
        f"{ctx.help_target.name} ({format_duration(travel)} away)"
        if ctx.help_target.name
        else "a hospital, fire station or other place with power"
    )
    leave_in = remaining - travel - LEAVE_BUFFER_MINUTES

    def at(outage_minutes_from_plan: float) -> datetime:
        return to_real_time(ctx.plan_at, max(0.0, outage_minutes_from_plan), ctx.speed)

    if remaining <= 0:
        depleted_detail = (
            f"{device} has no battery or backup power. If you can't get power right away, call 911."
        )
    else:
        depleted_detail = (
            f"{device} is estimated to run out of power now. If you haven't reached power, "
            "call 911."
        )
    return [
        _milestone(
            "halfway",
            at(total / 2 - elapsed),
            "Halfway through battery power",
            f"About {format_duration(total / 2)} left for {device}. Get ready to leave if the "
            "power isn't back.",
            "warning",
        ),
        _milestone(
            "leave_by",
            at(leave_in),
            "Leave for a powered location" if leave_in > 0 else "Leave for a powered location now",
            f"Head to {destination} before batteries get low.",
            "warning",
        ),
        _milestone(
            "critical_low",
            at(remaining - CRITICAL_LOW_MINUTES),
            "30 minutes of power left"
            if remaining >= CRITICAL_LOW_MINUTES
            else "Less than 30 minutes of power left",
            f"{device} is close to running out. Call 911 if you can't reach power.",
            "critical",
        ),
        _milestone(
            "depleted", at(remaining), "Batteries may be empty", depleted_detail, "critical"
        ),
    ]


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


def _conserve_detail(plan: Plan) -> str:
    """Advice for the first minutes of an outage, including the plan's saver-mode tips."""
    tips = [r.title for r in plan.recommendations if r.kind == "saver_mode"]
    detail = "Unplug everything that isn't medical and keep the fridge closed."
    if tips:
        detail += " Also: " + "; ".join(tips) + "."
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


# --- Check-ins: estimate where the batteries are now ------------------------------------


@dataclass(frozen=True, slots=True)
class BackupLevel:
    """A reported backup level at check-in."""

    charge_percent: float | None = None
    fuel_hours: float | None = None


def advance_state(
    devices: Sequence[DeviceInput],
    backups: Sequence[BackupInput],
    plan: Plan,
    elapsed_minutes: float,
) -> tuple[list[DeviceInput], list[BackupInput]]:
    """Estimate every battery's level after `elapsed_minutes` of the outage under `plan`.

    The planner only decides *how much* energy each device gets. To estimate levels part-way
    through we also need an order, and we use the one that makes the plan's sums work:

    1. Generators and car inverters first, all at once (a generator runs everything plugged
       into it at the same time, so its fuel hours count once for all of them).
    2. Then each device's own battery: internal first, then spares.
    3. Then its share of the shared batteries, drawn proportionally.

    Re-planning from the estimated state gives (up to rounding) the old runtime minus the
    elapsed time, so a check-in with no new readings leaves the countdown where it was.
    """
    by_id = {p.device_id: p for p in plan.devices}
    fuel_ids = {b.id for b in backups if b.is_fuel}
    drawn_wh: dict[str, float] = {}
    fuel_used_minutes: dict[str, float] = {}
    new_devices = []

    for device in devices:
        entry = by_id.get(device.id)
        if entry is None:
            new_devices.append(device)
            continue
        left = elapsed_minutes
        for allocation in entry.allocations:
            if allocation.backup_id in fuel_ids:
                used = min(left, allocation.minutes)
                previous = fuel_used_minutes.get(allocation.backup_id, 0.0)
                fuel_used_minutes[allocation.backup_id] = max(previous, used)
                left -= used

        own_minutes = min(left, entry.internal_minutes)
        left -= own_minutes
        new_devices.append(
            _drain_own_batteries(device, own_minutes * entry.effective_draw_watts / 60)
        )

        shared = [a for a in entry.allocations if a.backup_id not in fuel_ids]
        shared_minutes = sum(a.minutes for a in shared)
        share = min(1.0, left / shared_minutes) if shared_minutes > 0 else 0.0
        for allocation in shared:
            drawn_wh[allocation.backup_id] = (
                drawn_wh.get(allocation.backup_id, 0.0) + allocation.energy_wh * share
            )

    new_backups = []
    for backup in backups:
        if backup.is_fuel:
            used_hours = fuel_used_minutes.get(backup.id, 0.0) / 60
            new_backups.append(
                replace(backup, fuel_hours=max(0.0, (backup.fuel_hours or 0.0) - used_hours))
            )
        elif backup.capacity_wh:
            left_wh = max(0.0, backup.usable_wh - drawn_wh.get(backup.id, 0.0))
            new_backups.append(replace(backup, charge_percent=100 * left_wh / backup.capacity_wh))
        else:
            new_backups.append(backup)
    return new_devices, new_backups


def _drain_own_batteries(device: DeviceInput, used_wh: float) -> DeviceInput:
    """Take `used_wh` of usable energy from the internal battery, then from spares."""
    internal_usable = device.internal_battery_wh * device.charge_percent / 100
    internal_usable *= USABLE_BATTERY_FRACTION
    from_internal = min(used_wh, internal_usable)
    from_spares = used_wh - from_internal
    charge = device.charge_percent
    if device.internal_battery_wh > 0:
        full_usable = device.internal_battery_wh * USABLE_BATTERY_FRACTION
        charge = 100 * (internal_usable - from_internal) / full_usable
    spares = max(0.0, device.spare_batteries_wh - from_spares / USABLE_BATTERY_FRACTION)
    return replace(device, charge_percent=max(0.0, charge), spare_batteries_wh=spares)


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
