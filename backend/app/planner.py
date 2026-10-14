"""Battery runtime planner.

Given a household's medical devices and backup power, work out how long each device can keep
running once the grid goes down, and what the family could do to stretch that time.

Everything here is a pure function: no database, network or clock. The caller passes in all the
facts (including `now`) and gets back a `Plan`. The rules, also listed in `docs/API.md`:

1. Effective draw = (saver draw if saver mode is on, else normal draw) x duty cycle.
2. Each device first uses its own internal and spare batteries (x charge %, 90% usable).
3. Shared battery backups (power stations, UPS units) are split to maximise the shortest
   runtime among life-sustaining devices ("water-filling"); other devices then share what is
   left the same way. AC devices lose 15% to the inverter, and a backup can't power an AC
   device without an AC outlet, or any device that draws more than its maximum output.
4. Generators and car inverters add `fuel_hours` of runtime to every device they can run.
5. A device with no battery and no usable backup gets status `no_backup` and 0 minutes.
"""

import math
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import combinations

from app.catalog import BACKUP_CATALOG, FUEL_BACKUP_TYPES, device_spec
from app.schemas import (
    Allocation,
    BackupPlan,
    DevicePlan,
    DeviceStatus,
    Plan,
    Recommendation,
)
from app.timeutil import format_duration
from app.utilities_data import find_programs

INVERTER_EFFICIENCY = 0.85
"""Share of a battery's energy that reaches an AC device through the inverter."""
USABLE_BATTERY_FRACTION = 0.9
"""Share of a device battery's charge that is usable before it cuts out."""
_TOLERANCE = 1e-6
_SUGGESTED_STATION_ID = "suggested_power_station"


@dataclass(frozen=True, slots=True)
class DeviceInput:
    """A medical device as the planner sees it (mirrors the API's Device)."""

    id: str
    kind: str
    label: str
    life_sustaining: bool
    draw_watts: float
    saver_draw_watts: float | None
    saver_enabled: bool
    duty_cycle: float
    ac_powered: bool
    internal_battery_wh: float
    spare_batteries_wh: float
    charge_percent: float


@dataclass(frozen=True, slots=True)
class BackupInput:
    """A shared backup power source (mirrors the API's BackupSource)."""

    id: str
    type: str
    label: str
    capacity_wh: float | None
    max_output_watts: float
    has_ac_outlet: bool
    charge_percent: float | None
    fuel_hours: float | None

    @property
    def is_fuel(self) -> bool:
        """True for generators and car inverters, which are limited by fuel, not a battery."""
        return self.type in FUEL_BACKUP_TYPES

    @property
    def usable_wh(self) -> float:
        """Energy a battery backup holds right now (0 for fuel-based backups)."""
        if self.is_fuel or self.capacity_wh is None:
            return 0.0
        charge = 100.0 if self.charge_percent is None else self.charge_percent
        return self.capacity_wh * charge / 100


@dataclass(frozen=True, slots=True)
class HouseholdFacts:
    """Facts about the household that only shape the recommendations."""

    medical_program_registered: bool = True
    caregiver_count: int = 1
    has_location: bool = True
    utility_name: str | None = None
    state: str | None = None


def running_watts(device: DeviceInput, *, saver_all: bool = False) -> float:
    """What the device draws while switched on, using its saver setting when that applies."""
    saver = device.saver_draw_watts
    if (saver_all or device.saver_enabled) and saver is not None and saver < device.draw_watts:
        return saver
    return device.draw_watts


def effective_draw_watts(device: DeviceInput, *, saver_all: bool = False) -> float:
    """Average draw: running watts x duty cycle (rule 1)."""
    return running_watts(device, saver_all=saver_all) * device.duty_cycle


def own_battery_wh(device: DeviceInput) -> float:
    """Usable energy in the device's own batteries (rule 2).

    The internal battery counts at its last known charge; spare batteries are assumed full.
    """
    stored = device.internal_battery_wh * device.charge_percent / 100 + device.spare_batteries_wh
    return stored * USABLE_BATTERY_FRACTION


def incompatibility(backup: BackupInput, device: DeviceInput, watts: float) -> str | None:
    """Why `backup` can't power `device` (drawing `watts` while on), or None if it can."""
    if device.ac_powered and not backup.has_ac_outlet:
        return f"{backup.label} has no AC outlet"
    if watts > backup.max_output_watts + _TOLERANCE:
        return (
            f"{backup.label} can't run it (needs {_watts(watts)} W, "
            f"limit {_watts(backup.max_output_watts)} W)"
        )
    return None


# --- Allocation --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Load:
    """One device as the allocator sees it. Runtimes are in minutes."""

    device: DeviceInput
    running_watts: float
    average_watts: float
    own_minutes: float
    fuel_minutes: float
    wh_per_minute: float
    """Battery-backup energy one minute of runtime costs, including any inverter loss."""
    sources: frozenset[str]
    """Battery backups that can power this device."""

    @property
    def base_minutes(self) -> float:
        """Runtime before any shared battery energy: own batteries plus fuel."""
        return self.own_minutes + self.fuel_minutes


@dataclass(frozen=True, slots=True)
class _Outcome:
    """Result of one allocation run."""

    loads: tuple[_Load, ...]
    usable_wh: dict[str, float]
    flows: dict[str, dict[str, float]]
    """device id -> battery backup id -> Wh taken from that backup."""
    fuel: dict[str, dict[str, float]]
    """device id -> fuel backup id -> minutes it runs the device."""
    fuel_overflow: dict[str, list[str]]
    """device id -> fuel backups it fits individually but that were already fully loaded."""
    totals: dict[str, float]

    @property
    def bottleneck(self) -> str | None:
        """The life-sustaining device that runs out first (None if there are none)."""
        critical = [load.device.id for load in self.loads if load.device.life_sustaining]
        return min(critical, key=lambda device_id: self.totals[device_id]) if critical else None

    @property
    def critical_runtime(self) -> float | None:
        """Shortest runtime among life-sustaining devices (None if there are none)."""
        bottleneck = self.bottleneck
        return None if bottleneck is None else self.totals[bottleneck]


def _allocate(
    devices: Sequence[DeviceInput], backups: Sequence[BackupInput], *, saver_all: bool
) -> _Outcome:
    """Share out all backup power and return every device's runtime."""
    watts = {d.id: running_watts(d, saver_all=saver_all) for d in devices}
    batteries = [b for b in backups if not b.is_fuel]
    usable = {b.id: b.usable_wh for b in batteries}
    fuel, fuel_overflow = _assign_fuel(devices, [b for b in backups if b.is_fuel], watts)

    loads = []
    for device in devices:
        average = effective_draw_watts(device, saver_all=saver_all)
        efficiency = INVERTER_EFFICIENCY if device.ac_powered else 1.0
        sources = frozenset(
            b.id
            for b in batteries
            if usable[b.id] > _TOLERANCE and incompatibility(b, device, watts[device.id]) is None
        )
        loads.append(
            _Load(
                device=device,
                running_watts=watts[device.id],
                average_watts=average,
                own_minutes=own_battery_wh(device) / average * 60,
                fuel_minutes=sum(fuel[device.id].values()),
                wh_per_minute=average / 60 / efficiency,
                sources=sources,
            )
        )

    # Life-sustaining devices are levelled first; everything else shares what they leave.
    critical = [load for load in loads if load.device.life_sustaining]
    others = [load for load in loads if not load.device.life_sustaining]
    levels = _water_fill(critical, usable, committed=[])
    committed = [
        (load.sources, load.wh_per_minute * (levels[load.device.id] - load.base_minutes))
        for load in critical
    ]
    levels |= _water_fill(others, usable, committed)

    demands = {
        load.device.id: load.wh_per_minute * (levels[load.device.id] - load.base_minutes)
        for load in loads
    }
    flows = _route(loads, demands, usable)
    totals = {
        load.device.id: load.base_minutes + sum(flows[load.device.id].values()) / load.wh_per_minute
        for load in loads
    }
    return _Outcome(
        loads=tuple(loads),
        usable_wh=usable,
        flows=flows,
        fuel=fuel,
        fuel_overflow=fuel_overflow,
        totals=totals,
    )


def _assign_fuel(
    devices: Sequence[DeviceInput],
    fuel_backups: Sequence[BackupInput],
    watts: Mapping[str, float],
) -> tuple[dict[str, dict[str, float]], dict[str, list[str]]]:
    """Decide which devices each generator / car inverter runs, and for how long (rule 4).

    A generator runs everything plugged into it at the same time for its fuel hours, so it isn't
    "shared out" like a battery: each device it can run gets the full fuel time. What limits it
    is its output: devices are plugged in life-sustaining first, then smallest first, until the
    combined running watts would exceed `max_output_watts`.
    """
    fuel: dict[str, dict[str, float]] = {d.id: {} for d in devices}
    overflow: dict[str, list[str]] = {d.id: [] for d in devices}
    for backup in fuel_backups:
        if not backup.fuel_hours:
            continue
        spare_output = backup.max_output_watts
        for device in sorted(devices, key=lambda d: (not d.life_sustaining, watts[d.id])):
            if incompatibility(backup, device, watts[device.id]) is not None:
                continue
            if watts[device.id] <= spare_output + _TOLERANCE:
                fuel[device.id][backup.id] = backup.fuel_hours * 60
                spare_output -= watts[device.id]
            else:
                overflow[device.id].append(backup.label)
    return fuel, overflow


def _water_fill(
    group: Sequence[_Load],
    usable: Mapping[str, float],
    committed: Sequence[tuple[frozenset[str], float]],
) -> dict[str, float]:
    """Raise the runtimes in `group` as evenly as the shared batteries allow (rule 3).

    Picture each device as a glass of water whose level is its runtime: the device's own
    batteries and fuel fill it to its "base"; shared battery energy is poured into the lowest
    glasses first, so their levels rise together. This maximises the shortest runtime, then the
    next shortest, and so on (max-min fairness).

    The catch is that not every battery can reach every device (no AC outlet, output too
    small). Hall's theorem gives the exact limit: a common level L is reachable if, for every
    set T of batteries, the devices that can *only* use batteries in T need no more than T
    holds. So for each T we solve for the level its energy can support, take the lowest
    (the bottleneck), freeze the devices that depend only on that T, and repeat for the rest.
    Households have a handful of batteries, so checking every subset T is cheap.

    `committed` lists energy already promised to higher-priority devices, as (batteries that
    device may use, Wh). Returns device id -> target runtime in minutes.
    """
    levels = {load.device.id: load.base_minutes for load in group}
    active = [load for load in group if load.sources]
    fixed = list(committed)
    subsets = _subsets(usable)

    while active:
        lowest = math.inf
        tight: list[frozenset[str]] = []
        for subset in subsets:
            members = [load for load in active if load.sources <= subset]
            if not members:
                continue
            budget = sum(usable[b] for b in subset) - sum(wh for src, wh in fixed if src <= subset)
            level = _level_for_budget(members, max(budget, 0.0))
            if level < lowest - _TOLERANCE:
                lowest, tight = level, [subset]
            elif level <= lowest + _TOLERANCE:
                tight.append(subset)

        # Devices relying only on an exhausted set of batteries can't rise any further.
        frozen = [load for load in active if any(load.sources <= subset for subset in tight)]
        for load in frozen:
            level = max(load.base_minutes, lowest)
            levels[load.device.id] = level
            fixed.append((load.sources, load.wh_per_minute * (level - load.base_minutes)))
        active = [load for load in active if load not in frozen]
    return levels


def _subsets(usable: Mapping[str, float]) -> list[frozenset[str]]:
    """Every non-empty set of battery backups."""
    ids = list(usable)
    return [
        frozenset(combo) for size in range(1, len(ids) + 1) for combo in combinations(ids, size)
    ]


def _level_for_budget(members: Sequence[_Load], budget_wh: float) -> float:
    """The highest common runtime level `budget_wh` can lift `members` to.

    Solves sum(rate_i * max(0, L - base_i)) = budget for L. The left side is piecewise linear
    in L, so walk the devices in order of base runtime: once the water passes a device's base,
    that device starts drinking at its rate.
    """
    ordered = sorted(members, key=lambda load: load.base_minutes)
    rate = 0.0  # Wh per minute of level, summed over devices already under water
    weighted_base = 0.0  # sum of rate_i * base_i over the same devices
    for index, load in enumerate(ordered):
        rate += load.wh_per_minute
        weighted_base += load.wh_per_minute * load.base_minutes
        level = (budget_wh + weighted_base) / rate
        next_base = ordered[index + 1].base_minutes if index + 1 < len(ordered) else math.inf
        if level <= next_base:
            return level
    raise AssertionError("unreachable: the last device has no upper bound")


_Node = tuple[str, str]


def _route(
    loads: Sequence[_Load], demands: Mapping[str, float], usable: Mapping[str, float]
) -> dict[str, dict[str, float]]:
    """Decide how much energy each battery gives each device to meet `demands` (Wh).

    The water-filling levels are feasible by construction, so a maximum flow through
    source -> battery (its usable Wh) -> device (if compatible) -> sink (the device's demand)
    delivers them. Edmonds-Karp (shortest augmenting paths) on a graph this small is instant.
    """
    source: _Node = ("source", "")
    sink: _Node = ("sink", "")
    residual: dict[_Node, dict[_Node, float]] = {source: {}, sink: {}}

    def add_edge(start: _Node, end: _Node, capacity: float) -> None:
        residual.setdefault(start, {})[end] = capacity
        residual.setdefault(end, {}).setdefault(start, 0.0)

    unlimited = sum(usable.values()) + 1.0
    for backup_id, wh in usable.items():
        add_edge(source, ("backup", backup_id), wh)
    for load in loads:
        demand = demands[load.device.id]
        if demand <= _TOLERANCE:
            continue
        add_edge(("device", load.device.id), sink, demand)
        for backup_id in load.sources:
            add_edge(("backup", backup_id), ("device", load.device.id), unlimited)

    while path := _augmenting_path(residual, source, sink):
        push = min(residual[start][end] for start, end in path)
        for start, end in path:
            residual[start][end] -= push
            residual[end][start] += push

    # Flow sent battery -> device shows up as capacity on the reverse edge device -> battery.
    flows: dict[str, dict[str, float]] = {}
    for load in loads:
        node = ("device", load.device.id)
        flows[load.device.id] = {
            backup_id: wh
            for backup_id in sorted(load.sources)
            if (wh := residual.get(node, {}).get(("backup", backup_id), 0.0)) > _TOLERANCE
        }
    return flows


def _augmenting_path(
    residual: Mapping[_Node, Mapping[_Node, float]], source: _Node, sink: _Node
) -> list[tuple[_Node, _Node]] | None:
    """Shortest path from source to sink using only edges with spare capacity (BFS)."""
    parent: dict[_Node, _Node | None] = {source: None}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for neighbour, capacity in residual[node].items():
            if capacity <= _TOLERANCE or neighbour in parent:
                continue
            parent[neighbour] = node
            if neighbour == sink:
                path = []
                current: _Node = sink
                while (previous := parent[current]) is not None:
                    path.append((previous, current))
                    current = previous
                return path[::-1]
            queue.append(neighbour)
    return None


# --- Plan assembly -----------------------------------------------------------------------


def build_plan(
    devices: Sequence[DeviceInput],
    backups: Sequence[BackupInput],
    facts: HouseholdFacts,
    *,
    now: datetime,
    saver_preview: bool = False,
) -> Plan:
    """Compute the full plan: per-device runtimes, backup usage, recommendations, assumptions.

    With `saver_preview`, every device with a lower-power setting is assumed to use it.
    """
    outcome = _allocate(devices, backups, saver_all=saver_preview)
    device_plans = [_device_plan(load, outcome, backups) for load in outcome.loads]
    critical = outcome.critical_runtime
    return Plan(
        computed_at=now,
        assumes_saver_mode=saver_preview,
        critical_runtime_minutes=None if critical is None else _round(critical),
        bottleneck_device_id=outcome.bottleneck,
        devices=device_plans,
        backups=[_backup_plan(backup, outcome) for backup in backups],
        recommendations=_recommendations(devices, backups, facts, outcome, saver_preview),
        assumptions=_assumptions(devices, backups, outcome, saver_preview),
    )


def _device_plan(load: _Load, outcome: _Outcome, backups: Sequence[BackupInput]) -> DevicePlan:
    device = load.device
    allocations = [
        Allocation(
            backup_id=backup_id,
            energy_wh=_round(wh),
            minutes=_round(wh / load.wh_per_minute),
        )
        for backup_id, wh in outcome.flows[device.id].items()
    ]
    allocations += [
        Allocation(
            backup_id=backup_id,
            energy_wh=_round(load.average_watts * fuel_minutes / 60),
            minutes=_round(fuel_minutes),
        )
        for backup_id, fuel_minutes in outcome.fuel[device.id].items()
    ]
    total = outcome.totals[device.id]
    status, detail = _status(load, outcome, backups, total)
    return DevicePlan(
        device_id=device.id,
        effective_draw_watts=_round(load.average_watts),
        internal_minutes=_round(load.own_minutes),
        total_minutes=_round(total),
        allocations=allocations,
        status=status,
        status_detail=detail,
    )


def _status(
    load: _Load, outcome: _Outcome, backups: Sequence[BackupInput], total: float
) -> tuple[DeviceStatus, str | None]:
    """`no_backup` if nothing can run it, `limited` if some backup power can't reach it."""
    reasons = [
        reason
        for backup in backups
        if (reason := incompatibility(backup, load.device, load.running_watts)) is not None
    ]
    reasons += [
        f"{label} is already fully loaded by other devices"
        for label in outcome.fuel_overflow[load.device.id]
    ]
    # A non-critical device that could use a shared battery but got nothing from it.
    starved = (
        not load.device.life_sustaining
        and outcome.bottleneck is not None
        and bool(load.sources)
        and not outcome.flows[load.device.id]
    )
    if total <= _TOLERANCE:
        detail = (
            "Life-sustaining devices need all the shared backup power, so none is left for it."
            if starved
            else "Nothing can power it once the grid is down: no charged battery or usable backup."
        )
        return "no_backup", _join_sentences(detail, *reasons)
    if reasons:
        return "limited", _join_sentences(*reasons)
    if starved:
        return "limited", "Shared backup power is saved for life-sustaining devices first."
    return "ok", None


def _backup_plan(backup: BackupInput, outcome: _Outcome) -> BackupPlan:
    if backup.is_fuel:
        delivered = sum(
            load.average_watts * outcome.fuel[load.device.id].get(backup.id, 0.0) / 60
            for load in outcome.loads
        )
        return BackupPlan(
            backup_id=backup.id,
            usable_wh=_round(delivered),
            allocated_wh=_round(delivered),
            unused_wh=0.0,
            fuel_minutes=_round((backup.fuel_hours or 0.0) * 60),
        )
    usable = outcome.usable_wh[backup.id]
    allocated = sum(flows.get(backup.id, 0.0) for flows in outcome.flows.values())
    return BackupPlan(
        backup_id=backup.id,
        usable_wh=_round(usable),
        allocated_wh=_round(allocated),
        unused_wh=_round(max(0.0, usable - allocated)),
    )


# --- Recommendations ---------------------------------------------------------------------


def _recommendations(
    devices: Sequence[DeviceInput],
    backups: Sequence[BackupInput],
    facts: HouseholdFacts,
    outcome: _Outcome,
    saver_preview: bool,
) -> list[Recommendation]:
    """What the family could do, each with its real effect (the planner re-run with the change).

    Safety warnings come first, then changes ranked by the critical runtime they add, then
    preparation steps that don't change runtime.
    """

    def gain_if(
        new_devices: Sequence[DeviceInput], new_backups: Sequence[BackupInput]
    ) -> tuple[_Outcome, float | None]:
        trial = _allocate(new_devices, new_backups, saver_all=saver_preview)
        before, after = outcome.critical_runtime, trial.critical_runtime
        gain = None if before is None or after is None else _round(max(0.0, after - before))
        return trial, gain

    recs: list[Recommendation] = []
    fuel_backups = [b for b in backups if b.is_fuel]
    if fuel_backups:
        recs.append(
            Recommendation(
                id="co_safety",
                kind="safety",
                title="Run generators and cars outdoors only",
                detail="Exhaust contains carbon monoxide, which can kill in minutes. Keep "
                "generators outside and at least 20 feet from doors and windows, never run a car "
                "in a garage, and put a battery-powered CO alarm near where people sleep.",
                gain_minutes=None,
            )
        )

    if not saver_preview:
        for device in devices:
            saver = device.saver_draw_watts
            if device.saver_enabled or saver is None or saver >= device.draw_watts:
                continue
            trial, gain = gain_if(
                [replace(d, saver_enabled=True) if d.id == device.id else d for d in devices],
                backups,
            )
            own_gain = trial.totals[device.id] - outcome.totals[device.id]
            if own_gain <= 0.05 and not gain:
                continue
            spec = device_spec(device.kind)
            action = spec.saver_action or "Use the lower-power setting on {label}"
            effect = (
                f"adding about {format_duration(gain)} before the first life-sustaining device "
                "runs out"
                if gain and device.life_sustaining
                else f"so it runs about {format_duration(own_gain)} longer"
            )
            recs.append(
                Recommendation(
                    id=f"saver_{device.id}",
                    kind="saver_mode",
                    title=action.format(label=device.label),
                    detail=_join_sentences(
                        f"Cuts its power use from {_watts(device.draw_watts)} W to "
                        f"{_watts(saver)} W, {effect}",
                        spec.saver_tip or "",
                    ),
                    gain_minutes=gain if device.life_sustaining else None,
                )
            )

    if outcome.critical_runtime is not None:
        station = BACKUP_CATALOG["power_station"]
        capacity = station.typical_capacity_wh or 1000
        suggested = BackupInput(
            id=_SUGGESTED_STATION_ID,
            type="power_station",
            label="A new power station",
            capacity_wh=capacity,
            max_output_watts=station.typical_max_output_watts,
            has_ac_outlet=True,
            charge_percent=100,
            fuel_hours=None,
        )
        _, gain = gain_if(devices, [*backups, suggested])
        if gain and gain >= 1:
            recs.append(
                Recommendation(
                    id="add_power_station",
                    kind="add_backup",
                    title=f"Add a ~{capacity:,.0f} Wh power station",
                    detail=f"A charged {capacity:,.0f} Wh power station would keep life-sustaining "
                    f"devices running about {format_duration(gain)} longer. Keep it charged and "
                    "plug in life-sustaining devices first.",
                    gain_minutes=gain,
                )
            )

    low_devices = {
        d.id: d.charge_percent
        for d in devices
        if d.internal_battery_wh > 0 and d.charge_percent < 99.5
    }
    low_backups = {
        b.id: b.charge_percent
        for b in backups
        if not b.is_fuel and b.capacity_wh and b.charge_percent is not None
        if b.charge_percent < 99.5
    }
    if low_devices or low_backups:
        _, gain = gain_if(
            [replace(d, charge_percent=100) if d.id in low_devices else d for d in devices],
            [replace(b, charge_percent=100) if b.id in low_backups else b for b in backups],
        )
        levels = [
            f"{d.label} is at {low_devices[d.id]:.0f}%" for d in devices if d.id in low_devices
        ]
        levels += [
            f"{b.label} is at {low_backups[b.id]:.0f}%" for b in backups if b.id in low_backups
        ]
        effect = (
            f"Charging them fully adds about {format_duration(gain)} for life-sustaining devices."
            if gain
            else "Keep them topped up so they're ready when the power goes out."
        )
        recs.append(
            Recommendation(
                id="charge_batteries",
                kind="charge",
                title="Charge batteries that aren't full",
                detail=_join_sentences(", ".join(levels), effect),
                gain_minutes=gain,
            )
        )

    recs.sort(key=lambda r: (r.kind != "safety", r.gain_minutes is None, -(r.gain_minutes or 0)))
    recs += _preparation_steps(facts)
    return recs


def _preparation_steps(facts: HouseholdFacts) -> list[Recommendation]:
    """Steps that don't add runtime but make the family safer."""
    steps: list[Recommendation] = []
    if not facts.medical_program_registered:
        programs = find_programs(facts.state, facts.utility_name) if facts.utility_name else []
        if programs:
            program = programs[0]
            title = f"Sign up for {program.utility}'s {program.program_name}"
            detail = program.what_it_does
        else:
            utility = facts.utility_name or "your utility"
            title = f"Register with {utility} as a medical customer"
            detail = (
                "Most utilities keep a list of homes with life-sustaining equipment and warn "
                "them before planned shutoffs."
            )
        steps.append(
            Recommendation(
                id="register_program",
                kind="register_program",
                title=title,
                detail=_join_sentences(
                    detail, "LifeLine Power can draft the letter for your clinician to sign."
                ),
                gain_minutes=None,
            )
        )
    if facts.caregiver_count == 0:
        steps.append(
            Recommendation(
                id="invite_caregiver",
                kind="caregiver",
                title="Invite a caregiver",
                detail="Share your join code so a family member or friend is alerted the moment "
                "the power goes out, even if you can't call.",
                gain_minutes=None,
            )
        )
    if not facts.has_location:
        steps.append(
            Recommendation(
                id="set_location",
                kind="location",
                title="Add your home's location",
                detail="So we can find the nearest place with power and work out when you would "
                "need to leave.",
                gain_minutes=None,
            )
        )
    return steps


# --- Assumptions -------------------------------------------------------------------------


def _assumptions(
    devices: Sequence[DeviceInput],
    backups: Sequence[BackupInput],
    outcome: _Outcome,
    saver_preview: bool,
) -> list[str]:
    """Plain-English list of what the numbers assume, so families can judge them."""
    notes: list[str] = []
    if saver_preview:
        notes.append("Preview: every device with a lower-power setting is assumed to be using it.")
    if any(d.internal_battery_wh > 0 or d.spare_batteries_wh > 0 for d in devices):
        notes.append(
            "Device batteries: 90% of their charge counts as usable, because batteries cut off "
            "before they are completely empty."
        )
    if any(d.spare_batteries_wh > 0 for d in devices):
        notes.append("Spare batteries are counted as fully charged.")
    batteries = [b for b in backups if not b.is_fuel]
    if batteries and any(d.ac_powered for d in devices):
        notes.append(
            "AC devices plugged into a power station or UPS lose about 15% of the energy to the "
            "inverter."
        )
    if batteries and any(d.life_sustaining for d in devices):
        notes.append(
            "Shared backup power is split so life-sustaining devices last as long as possible; "
            "other devices get what is left."
        )
    for backup in backups:
        if backup.is_fuel and backup.fuel_hours:
            notes.append(
                f"{backup.label} counts as one tank of fuel: about "
                f"{format_duration(backup.fuel_hours * 60)}. Refueling extends this."
            )
    if any(d.duty_cycle < 1 for d in devices):
        notes.append(
            "Devices that run only part of the day (like a fridge or a CPAP at night) are counted "
            "at their average draw."
        )
    notes += _overload_warnings(batteries, outcome)
    notes.append(
        "These are estimates from typical figures. Check your device manuals and update the "
        "numbers for the best estimate."
    )
    return notes


def _overload_warnings(batteries: Iterable[BackupInput], outcome: _Outcome) -> list[str]:
    """Warn when everything sharing one battery would exceed its output if run at once."""
    warnings = []
    for backup in batteries:
        total = sum(
            load.running_watts
            for load in outcome.loads
            if backup.id in outcome.flows[load.device.id]
        )
        if total > backup.max_output_watts + _TOLERANCE:
            warnings.append(
                f"{backup.label} can't run all its devices at once ({_watts(total)} W together, "
                f"limit {_watts(backup.max_output_watts)} W). Plug in life-sustaining devices "
                "first."
            )
    return warnings


# --- Small helpers -----------------------------------------------------------------------


def _round(value: float) -> float:
    """Round to one decimal place (and turn -0.0 into 0.0)."""
    return round(value, 1) + 0.0


def _watts(value: float) -> str:
    """`300` for whole watts, `0.8` otherwise."""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def _join_sentences(*parts: str) -> str:
    """Join fragments into sentences, adding full stops where missing."""
    sentences = []
    for part in parts:
        text = part.strip()
        if text:
            sentences.append(text if text.endswith((".", "!", "?")) else f"{text}.")
    return " ".join(sentences)
