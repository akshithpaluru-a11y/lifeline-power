"""Battery runtime planner.

Given a household's medical devices and backup power, work out how long each device can keep
running once the grid goes down, and what the family could do to stretch that time.

Everything here is a pure function: no database, network or clock. The caller passes in all the
facts (including `now`) and gets back a `Plan`. The rules, also listed in `docs/API.md`:

1. Effective draw = (saver draw if saver mode is on, else normal draw) x duty cycle. The
   exception is a life-sustaining device that can run for hours at a stretch (an overnight
   BiPAP, a dialysis cycler): the power can go out just as it is switched on, so it is counted
   at its full draw and the countdown can't run hours past the moment its battery dies.
2. Each device first uses its own internal and spare batteries (x charge %, 90% usable).
3. Shared battery backups (power stations, UPS units) are split to maximise the shortest
   runtime among life-sustaining devices ("water-filling"); other devices then share what is
   left the same way. AC devices lose 15% to the inverter, and a backup can't power an AC
   device without an AC outlet, or any device that draws more than its maximum output.
4. Generators and car inverters add `fuel_hours` of runtime to every device they run.
5. Everything plugged into one backup runs at once, so together it must fit within the
   backup's maximum output. Life-sustaining devices are plugged in first, choosing the ones that
   keep the shortest life-sustaining runtime as long as possible. During an outage, re-plans
   keep the arrangement the family was already shown.
6. A device with no battery and no usable backup gets status `no_backup` and 0 minutes.
"""

import math
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import accumulate, combinations, islice, product
from math import prod

from app.catalog import BACKUP_CATALOG, FUEL_BACKUP_TYPES, device_spec
from app.schemas import (
    Allocation,
    BackupPlan,
    DevicePlan,
    DeviceStatus,
    Plan,
    PlugStep,
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
_MAX_PLUG_CHOICES = 4096
"""How many ways of sharing out overloaded backups the planner compares, at most (rule 5).
In practice `_PLUG_SEARCH_WORK` is the limit: a typical home has every way compared."""
_REARRANGE_GAIN_MINUTES = 30.0
"""During an outage, plugs are only moved if that adds at least this much critical runtime."""
_PLUG_SEARCH_WORK = 131_072
"""Work budget for that comparison (see `_choice_limit`), so big households stay fast."""
_MAX_FILL_STEPS = 2_000
"""Search steps allowed when listing the ways to fill one overloaded backup."""


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


def average_draw_watts(device: DeviceInput, *, saver_all: bool = False) -> float:
    """Draw averaged over a whole day: running watts x duty cycle."""
    return running_watts(device, saver_all=saver_all) * device.duty_cycle


def counted_at_full_draw(device: DeviceInput) -> bool:
    """True for a life-sustaining device the plan counts at its full draw despite its duty cycle.

    A BiPAP used 8 hours a night averages a third of its draw over a day, but if the outage
    starts as it is switched on, it drains its battery at full power until the battery is empty.
    Planning on the average would put the leave-by and call-911 alerts hours after the device
    has stopped. Only kinds known to run in short bursts spread through the day (suction, a
    fridge compressor) keep their average; for any other kind, including `custom` devices whose
    pattern we can't know, the cautious figure is used.
    """
    return (
        device.life_sustaining
        and device.duty_cycle < 1
        and not device_spec(device.kind).runs_in_short_bursts
    )


def effective_draw_watts(device: DeviceInput, *, saver_all: bool = False) -> float:
    """The draw the plan counts on (rule 1): the average draw, or full draw where that applies.

    The plan reports it as each device's `effective_draw_watts`, and the outage engine drains
    batteries at it between check-ins, so the countdown and those estimates agree.
    """
    if counted_at_full_draw(device):
        return running_watts(device, saver_all=saver_all)
    return average_draw_watts(device, saver_all=saver_all)


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

_Plugs = dict[str, frozenset[str]]
"""Which devices each backup runs: backup id -> ids of the devices plugged into it."""


@dataclass(frozen=True, slots=True)
class _Load:
    """One device as the allocator sees it. Runtimes are in minutes."""

    device: DeviceInput
    running_watts: float
    effective_watts: float
    """The draw the plan counts on (`effective_draw_watts`)."""
    own_minutes: float
    fuel_minutes: float
    wh_per_minute: float
    """Battery-backup energy one minute of runtime costs, including any inverter loss."""
    sources: frozenset[str]
    """Battery backups this device is plugged into."""

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
    fits_alone: _Plugs
    """Devices each backup could run if nothing else were plugged into it."""
    plugged: _Plugs
    """Devices each backup actually runs."""
    totals: dict[str, float]
    kept: Mapping[str, frozenset[str]] | None = None
    """The arrangement this plan was asked to keep (see `_allocate`), for what-if re-plans."""

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

    @property
    def feeding_critical(self) -> frozenset[str]:
        """Battery backups that give energy to at least one life-sustaining device."""
        return frozenset(
            backup_id
            for load in self.loads
            if load.device.life_sustaining
            for backup_id in self.flows[load.device.id]
        )


def _allocate(
    devices: Sequence[DeviceInput],
    backups: Sequence[BackupInput],
    *,
    saver_all: bool,
    keep: Mapping[str, frozenset[str]] | None = None,
) -> _Outcome:
    """Decide which backups run which devices, share out the batteries, return every runtime.

    `keep` is an earlier plan's arrangement (backup id -> device ids). Each backup keeps the
    devices it ran as long as it can still run them all, so a re-plan never quietly assumes the
    family has moved plugs around. Only devices with no live backup left (new ones, or ones
    whose backup ran dry) are placed again, in the room that is left.
    """
    usable = {b.id: b.usable_wh for b in backups if not b.is_fuel}
    unplugged = [_unplugged_load(device, saver_all=saver_all) for device in devices]
    fits_alone = {
        backup.id: frozenset(load.device.id for load in unplugged if _can_run(backup, load, usable))
        for backup in backups
    }

    # Life-sustaining devices are plugged in and levelled first (rules 3 and 5)...
    critical = [load for load in unplugged if load.device.life_sustaining]

    def critical_score(plugs: _Plugs) -> tuple[float, ...]:
        return _leximin(_water_fill(_plug_in(critical, backups, plugs), usable, committed=[]))

    room = {backup.id: backup.max_output_watts for backup in backups}
    critical_plugs = _choose_plugs(
        critical, backups, fits_alone, room, critical_score, _choice_limit(usable, critical), keep
    )
    plugged_critical = _plug_in(critical, backups, critical_plugs)
    levels = _water_fill(plugged_critical, usable, committed=[])
    committed = [
        (load.sources, load.wh_per_minute * (levels[load.device.id] - load.base_minutes))
        for load in plugged_critical
    ]

    # ...then everything else shares the output and the energy they leave.
    others = [load for load in unplugged if not load.device.life_sustaining]

    def others_score(plugs: _Plugs) -> tuple[float, ...]:
        return _leximin(_water_fill(_plug_in(others, backups, plugs), usable, committed))

    used = {
        backup_id: sum(load.running_watts for load in critical if load.device.id in ids)
        for backup_id, ids in critical_plugs.items()
    }
    room = {backup.id: backup.max_output_watts - used[backup.id] for backup in backups}
    other_plugs = _choose_plugs(
        others, backups, fits_alone, room, others_score, _choice_limit(usable, others), keep
    )
    plugged_others = _plug_in(others, backups, other_plugs)
    levels |= _water_fill(plugged_others, usable, committed)

    by_id = {load.device.id: load for load in (*plugged_critical, *plugged_others)}
    loads = tuple(by_id[device.id] for device in devices)
    demands = {
        load.device.id: load.wh_per_minute * (levels[load.device.id] - load.base_minutes)
        for load in loads
    }
    flows = _route(loads, demands, usable)
    totals = {
        load.device.id: load.base_minutes + sum(flows[load.device.id].values()) / load.wh_per_minute
        for load in loads
    }
    plugged = {backup.id: critical_plugs[backup.id] | other_plugs[backup.id] for backup in backups}
    fuel = {
        load.device.id: {
            backup.id: (backup.fuel_hours or 0.0) * 60
            for backup in backups
            if backup.is_fuel and load.device.id in plugged[backup.id]
        }
        for load in loads
    }
    return _Outcome(
        loads=loads,
        usable_wh=usable,
        flows=flows,
        fuel=fuel,
        fits_alone=fits_alone,
        plugged=plugged,
        totals=totals,
        kept=keep,
    )


def _unplugged_load(device: DeviceInput, *, saver_all: bool) -> _Load:
    """The device running on its own batteries, before any backup is plugged in."""
    effective = effective_draw_watts(device, saver_all=saver_all)
    efficiency = INVERTER_EFFICIENCY if device.ac_powered else 1.0
    return _Load(
        device=device,
        running_watts=running_watts(device, saver_all=saver_all),
        effective_watts=effective,
        own_minutes=own_battery_wh(device) / effective * 60,
        fuel_minutes=0.0,
        wh_per_minute=effective / 60 / efficiency,
        sources=frozenset(),
    )


def _can_run(backup: BackupInput, load: _Load, usable: Mapping[str, float]) -> bool:
    """Whether `backup` could run this device if nothing else were plugged into it."""
    if incompatibility(backup, load.device, load.running_watts) is not None:
        return False
    if backup.is_fuel:
        return (backup.fuel_hours or 0.0) > 0
    return usable[backup.id] > _TOLERANCE


def _plug_in(group: Sequence[_Load], backups: Sequence[BackupInput], plugs: _Plugs) -> list[_Load]:
    """`group` with each device's fuel time and battery sources set from `plugs` (rule 4).

    A generator runs everything plugged into it at the same time for its fuel hours, so it isn't
    shared out like a battery: every device on it gets the full fuel time.
    """
    plugged_in = []
    for load in group:
        on = [backup for backup in backups if load.device.id in plugs[backup.id]]
        plugged_in.append(
            replace(
                load,
                fuel_minutes=sum((b.fuel_hours or 0.0) * 60 for b in on if b.is_fuel),
                sources=frozenset(b.id for b in on if not b.is_fuel),
            )
        )
    return plugged_in


def _choose_plugs(
    group: Sequence[_Load],
    backups: Sequence[BackupInput],
    fits_alone: _Plugs,
    room: Mapping[str, float],
    score: Callable[[_Plugs], tuple[float, ...]],
    limit: int,
    keep: Mapping[str, frozenset[str]] | None = None,
) -> _Plugs:
    """Which devices of `group` each backup runs, with `room` watts of output left on each (rule 5).

    Everything plugged into a backup runs at the same time, so together it must fit within the
    backup's output. Where every device of the group that could use a backup fits, they all go
    in. Where they don't, simple rules go wrong: "smallest first" fills a car with devices that
    have batteries of their own and leaves out the one that has none. So each overloaded backup
    is filled in the ways that leave out nothing that would still fit, and the combination with
    the best `score` wins (the first tried wins ties).

    The first combination is a greedy one: each backup in turn takes devices not yet plugged
    into an earlier backup first, then those fewest backups can run, then those with the least
    battery of their own. When there are at most `limit` combinations, all are compared;
    otherwise the greedy one is improved one backup at a time, within `limit` comparisons, so
    even a large household is planned at once.

    With `keep` (see `_allocate`), a backup that can still run every device it ran keeps them,
    and only devices left with no live backup are candidates for its spare room.
    """
    keep = keep or {}
    options = {
        load.device.id: sum(load.device.id in fits_alone[b.id] for b in backups) for load in group
    }
    kept: _Plugs = {}
    for backup in backups:
        if not keep.get(backup.id):
            continue  # nothing was plugged into it: free to use, like a backup added mid-outage
        ids = frozenset(
            load.device.id
            for load in group
            if load.device.id in keep[backup.id] and load.device.id in fits_alone[backup.id]
        )
        watts = sum(load.running_watts for load in group if load.device.id in ids)
        if watts <= room[backup.id] + _TOLERANCE:
            kept[backup.id] = ids
    placed = frozenset().union(*kept.values())

    settled: _Plugs = {}
    open_ids: list[str] = []
    open_fills: list[list[frozenset[str]]] = []
    plugged_earlier: set[str] = set()
    for backup in backups:
        fixed = kept.get(backup.id, frozenset())
        candidates = sorted(
            (
                load
                for load in group
                if load.device.id in fits_alone[backup.id]
                and not (backup.id in kept and load.device.id in placed)
            ),
            key=lambda load: (
                load.device.id in plugged_earlier,
                options[load.device.id],
                load.own_minutes,
                load.running_watts,
            ),
        )
        left = room[backup.id] - sum(x.running_watts for x in group if x.device.id in fixed)
        if sum(load.running_watts for load in candidates) <= left + _TOLERANCE:
            fills = [fixed | frozenset(load.device.id for load in candidates)]
            settled[backup.id] = fills[0]
        else:
            fills = [fixed | fill for fill in islice(_fills(candidates, left), limit)]
            open_ids.append(backup.id)
            open_fills.append(fills)
        plugged_earlier |= fills[0]

    best = settled | {
        backup_id: fills[0] for backup_id, fills in zip(open_ids, open_fills, strict=True)
    }
    if prod(len(fills) for fills in open_fills) <= limit:
        trials = [
            settled | dict(zip(open_ids, choice, strict=True)) for choice in product(*open_fills)
        ]
        return trials[0] if len(trials) == 1 else max(trials, key=score)
    # Too many to compare them all: change one backup's fill at a time while that helps,
    # taking the backups in turn so the budget is spread across all of them.
    best_score = score(best)
    budget = limit - 1
    improved = True
    while improved and budget > 0:
        improved = False
        for index in range(max(len(fills) for fills in open_fills)):
            for backup_id, fills in zip(open_ids, open_fills, strict=True):
                if budget <= 0 or index >= len(fills) or fills[index] == best[backup_id]:
                    continue
                budget -= 1
                trial = best | {backup_id: fills[index]}
                trial_score = score(trial)
                if trial_score > best_score:
                    best, best_score, improved = trial, trial_score, True
    return best


def _choice_limit(usable: Mapping[str, float], group: Sequence[_Load]) -> int:
    """How many plug-in choices to compare for `group`, within `_PLUG_SEARCH_WORK`.

    Comparing a choice means water-filling it, which can check every set of charged batteries
    for every device, once per device it settles: about 2^(charged batteries) x devices^2.
    A household too big to compare any gets the greedy choice alone.
    """
    charged = sum(1 for wh in usable.values() if wh > _TOLERANCE)
    cost = (1 << charged) * max(1, len(group)) ** 2
    return max(1, min(_MAX_PLUG_CHOICES, _PLUG_SEARCH_WORK // cost))


def _fills(candidates: Sequence[_Load], room: float) -> Iterator[frozenset[str]]:
    """Ways to plug devices from `candidates` into `room` watts, leaving out none that would fit.

    Depth-first, trying "plug it in" before "leave it out", so the first fill is the greedy one
    in the candidates' order. Branches that can't end up full are cut, and the search stops
    after `_MAX_FILL_STEPS` steps (always after the greedy fill has been found).
    """
    # rest[i]: watts of candidates i onwards, the most that plugging in the rest could still use.
    rest = list(accumulate(reversed([load.running_watts for load in candidates]), initial=0.0))
    rest.reverse()
    chosen: list[str] = []
    steps = 0

    def visit(index: int, left: float, smallest_left_out: float) -> Iterator[frozenset[str]]:
        nonlocal steps
        steps += 1
        # Cut if a device already left out would still fit even with everything else plugged in.
        if steps > _MAX_FILL_STEPS or left - rest[index] >= smallest_left_out - _TOLERANCE:
            return
        if index == len(candidates):
            yield frozenset(chosen)
            return
        load = candidates[index]
        if load.running_watts <= left + _TOLERANCE:
            chosen.append(load.device.id)
            yield from visit(index + 1, left - load.running_watts, smallest_left_out)
            chosen.pop()
        yield from visit(index + 1, left, min(smallest_left_out, load.running_watts))

    yield from visit(0, room, math.inf)


def _leximin(levels: Mapping[str, float]) -> tuple[float, ...]:
    """Runtimes from shortest to longest (rounded against float noise), for comparing plans.

    Tuples compare item by item, so the larger one is the plan whose shortest runtime is
    longest, then whose next shortest is longest, and so on ("leximin").
    """
    return tuple(sorted(round(level, 6) for level in levels.values()))


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
    # Sorted by base runtime once, so members come out in the order _level_for_budget walks.
    active = sorted((load for load in group if load.sources), key=lambda load: load.base_minutes)
    # Energy each set of batteries holds that isn't promised to a device yet.
    budgets = {subset: sum(usable[b] for b in subset) for subset in _subsets(usable)}
    _promise(budgets, committed)

    while active:
        # Sets of batteries with the same members differ only in budget, and the smallest one
        # binds, so the level is solved once per distinct group of members.
        binding: dict[tuple[int, ...], float] = {}
        for subset, budget in budgets.items():
            members = tuple(i for i, load in enumerate(active) if load.sources <= subset)
            if members and budget < binding.get(members, math.inf):
                binding[members] = budget
        supported = {
            members: _level_for_budget([active[i] for i in members], max(budget, 0.0))
            for members, budget in binding.items()
        }
        lowest = min(supported.values())

        # Devices relying only on an exhausted set of batteries can't rise any further.
        frozen = {
            i
            for members, level in supported.items()
            if level <= lowest + _TOLERANCE
            for i in members
        }
        promised = []
        for index in sorted(frozen):
            load = active[index]
            level = max(load.base_minutes, lowest)
            levels[load.device.id] = level
            promised.append((load.sources, load.wh_per_minute * (level - load.base_minutes)))
        _promise(budgets, promised)
        active = [load for index, load in enumerate(active) if index not in frozen]
    return levels


def _promise(
    budgets: dict[frozenset[str], float], promised: Iterable[tuple[frozenset[str], float]]
) -> None:
    """Take energy promised to devices out of every battery set that holds all their sources."""
    for sources, wh in promised:
        for subset in budgets:
            if sources <= subset:
                budgets[subset] -= wh


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
    keep_plugs: Mapping[str, Iterable[str]] | None = None,
) -> Plan:
    """Compute the full plan: per-device runtimes, backup usage, recommendations, assumptions.

    With `saver_preview`, every device with a lower-power setting is assumed to use it.
    `keep_plugs` is the arrangement an earlier plan showed (`plugs_of`), kept where it still
    works (see `_allocate`).
    """
    keep = None if keep_plugs is None else {b: frozenset(ids) for b, ids in keep_plugs.items()}
    outcome = _allocate(devices, backups, saver_all=saver_preview, keep=keep)
    if keep is not None:
        # Moving plugs is worth it when it buys real time, e.g. a device added mid-outage that
        # nothing could run, or a car that was refuelled. The plug steps then say what to move.
        free = _allocate(devices, backups, saver_all=saver_preview)
        kept_runtime, free_runtime = outcome.critical_runtime, free.critical_runtime
        if (
            kept_runtime is not None
            and free_runtime is not None
            and free_runtime >= kept_runtime + _REARRANGE_GAIN_MINUTES
        ):
            outcome = free
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


def plugs_of(plan: Plan) -> dict[str, list[str]] | None:
    """The arrangement `plan` showed (backup id -> device ids), or None if it doesn't say."""
    if any(backup.device_ids is None for backup in plan.backups):
        return None
    return {backup.backup_id: backup.device_ids or [] for backup in plan.backups}


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
            energy_wh=_round(load.effective_watts * fuel_minutes / 60),
            minutes=_round(fuel_minutes),
        )
        for backup_id, fuel_minutes in outcome.fuel[device.id].items()
    ]
    total = outcome.totals[device.id]
    status, detail = _status(load, outcome, backups, total)
    return DevicePlan(
        device_id=device.id,
        effective_draw_watts=_round(load.effective_watts),
        internal_minutes=_round(load.own_minutes),
        total_minutes=_round(total),
        allocations=allocations,
        status=status,
        status_detail=detail,
        plug_steps=_plug_steps(load, outcome, backups),
    )


def _plug_steps(load: _Load, outcome: _Outcome, backups: Sequence[BackupInput]) -> list[PlugStep]:
    """What to plug the device into and when, in the order the plan uses its power sources.

    The plan's runtimes assume this order (the same one `outage_engine.advance_state` uses):
    generators and cars first, then the device's own batteries, then shared batteries. Each step
    after the first is triggered by something the family can see (a car runs out of fuel, the
    device's low-battery alarm, a station's display at 0%), so no clock is needed. A device that
    isn't life-sustaining only gets batteries no life-sustaining device needs (rule 3), so it can
    stay plugged in until they are empty too.
    """
    device_id = load.device.id
    fuel = [b for b in backups if b.id in outcome.fuel[device_id]]
    shared = [b for b in backups if outcome.flows[device_id].get(b.id, 0.0) > _TOLERANCE]
    if not fuel and not shared:
        return []
    steps: list[PlugStep] = []
    trigger: str | None = None  # what ends the previous step, e.g. "When Car runs out of fuel"
    on_backup = False
    for backup in fuel:
        if trigger is None:
            steps.append(
                PlugStep(text=f"Plug it into {backup.label} now.", backup_id=backup.id, now=True)
            )
        else:
            steps.append(
                PlugStep(
                    text=f"{trigger}, move it to {backup.label}.", backup_id=backup.id, now=False
                )
            )
        trigger, on_backup = f"When {backup.label} runs out of fuel", True
    if load.own_minutes > _TOLERANCE and shared:
        text = (
            "Start it on its own battery."
            if trigger is None
            else (f"{trigger}, it runs on its own battery.")
        )
        steps.append(PlugStep(text=text, backup_id=None, now=trigger is None))
        trigger, on_backup = "When its own battery runs low", False
    for backup in shared:
        if trigger is None:
            text, now = f"Plug it into {backup.label} now.", True
        else:
            verb = "move it to" if on_backup else "plug it into"
            text, now = f"{trigger}, {verb} {backup.label}.", False
        steps.append(PlugStep(text=text, backup_id=backup.id, now=now))
        trigger, on_backup = f"When {backup.label} is empty", True
    return steps


def _status(
    load: _Load, outcome: _Outcome, backups: Sequence[BackupInput], total: float
) -> tuple[DeviceStatus, str | None]:
    """`no_backup` if nothing can run it, `limited` if some backup power can't reach it."""
    device_id = load.device.id
    reasons = [
        reason
        for backup in backups
        if (reason := incompatibility(backup, load.device, load.running_watts)) is not None
    ]
    left_out = [
        f"{backup.label} is already fully loaded by other devices"
        for backup in backups
        if device_id in outcome.fits_alone[backup.id] - outcome.plugged[backup.id]
    ]
    reasons += left_out
    # A non-critical device plugged into shared batteries that all went to life-sustaining
    # devices. (If it got nothing because other non-critical devices would run out sooner, that
    # is the normal sharing, not something to blame on the life-sustaining devices.)
    starved = (
        not load.device.life_sustaining
        and bool(load.sources)
        and not outcome.flows[device_id]
        and load.sources <= outcome.feeding_critical
    )
    if total <= _TOLERANCE:
        detail = (
            "Life-sustaining devices need all the shared backup power, so none is left for it."
            if starved
            else "No backup power is left for it once the grid is down."
            if left_out
            else "Nothing can power it once the grid is down: no charged battery or usable backup."
        )
        return "no_backup", _join_sentences(detail, *reasons)
    if reasons:
        return "limited", _join_sentences(*reasons)
    if starved:
        return "limited", "Shared backup power is saved for life-sustaining devices first."
    return "ok", None


def _backup_plan(backup: BackupInput, outcome: _Outcome) -> BackupPlan:
    # The devices that use it at some point (each device's plug steps say when). A generator
    # runs everything plugged into it at once. A battery lists only devices that get some of
    # its energy: plugging in one the plan gives nothing (say a CPAP when the oxygen needs the
    # whole station) would drain energy the plan counts on.
    plugged = outcome.plugged[backup.id]
    device_ids = [
        load.device.id
        for load in outcome.loads
        if load.device.id in plugged
        and (backup.is_fuel or outcome.flows[load.device.id].get(backup.id, 0.0) > _TOLERANCE)
    ]
    if backup.is_fuel:
        delivered = sum(
            load.effective_watts * outcome.fuel[load.device.id].get(backup.id, 0.0) / 60
            for load in outcome.loads
        )
        return BackupPlan(
            backup_id=backup.id,
            usable_wh=_round(delivered),
            allocated_wh=_round(delivered),
            unused_wh=0.0,
            fuel_minutes=_round((backup.fuel_hours or 0.0) * 60),
            device_ids=device_ids,
        )
    usable = outcome.usable_wh[backup.id]
    allocated = sum(flows.get(backup.id, 0.0) for flows in outcome.flows.values())
    return BackupPlan(
        backup_id=backup.id,
        usable_wh=_round(usable),
        allocated_wh=_round(allocated),
        unused_wh=_round(max(0.0, usable - allocated)),
        device_ids=device_ids,
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
        """Re-plan with a change: the new outcome and the critical runtime it adds.

        The gain is negative if the change would shorten the critical runtime; each caller
        decides what to do about that rather than having it hidden as 0.
        """
        trial = _allocate(new_devices, new_backups, saver_all=saver_preview, keep=outcome.kept)
        before, after = outcome.critical_runtime, trial.critical_runtime
        gain = None if before is None or after is None else _round(after - before)
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
            # Never suggest a change that makes the first life-sustaining device run out sooner.
            if gain is not None and gain < 0:
                continue
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
        if gain is not None and gain < 0:
            # More charge can't shorten any runtime. A drop could only come from the capped
            # search over ways to plug devices in (rule 5), so it says nothing about charging.
            gain = None
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
    if any(d.duty_cycle < 1 and not counted_at_full_draw(d) for d in devices):
        notes.append(
            "Devices that run only part of the day (like a fridge) are counted at their average "
            "draw."
        )
    for device in devices:
        if counted_at_full_draw(device):
            watts = running_watts(device, saver_all=saver_preview)
            notes.append(
                f"{device.label} is counted at its full {_watts(watts)} W from the moment the "
                "power goes out, because it can run for hours at a stretch. Averaged over a day it "
                f"uses about {_watts(watts * device.duty_cycle)} W, so if it only runs part of "
                "the day its power may last longer than shown."
            )
    notes += _overload_warnings(backups, outcome)
    notes.append(
        "These are estimates from typical figures. Check your device manuals and update the "
        "numbers for the best estimate."
    )
    return notes


def _overload_warnings(backups: Iterable[BackupInput], outcome: _Outcome) -> list[str]:
    """Warn when the devices that could use one backup need more than its output together."""
    running = {load.device.id: load.running_watts for load in outcome.loads}
    critical = {load.device.id for load in outcome.loads if load.device.life_sustaining}
    warnings = []
    for backup in backups:
        users = outcome.fits_alone[backup.id]
        total = sum(running[device_id] for device_id in users)
        if total <= backup.max_output_watts + _TOLERANCE:
            continue
        limit = _watts(backup.max_output_watts)
        critical_total = sum(running[device_id] for device_id in users & critical)
        if critical_total > backup.max_output_watts + _TOLERANCE:
            warnings.append(
                f"{backup.label} can't run all its devices at once: the life-sustaining ones "
                f"alone need {_watts(critical_total)} W, over its {limit} W limit. The plan counts "
                "only the ones it can run together; the rest rely on their own batteries or other "
                "backups."
            )
        else:
            warnings.append(
                f"{backup.label} can't run all its devices at once ({_watts(total)} W together, "
                f"limit {limit} W). The plan plugs in life-sustaining devices first."
            )
    return warnings


# --- Small helpers -----------------------------------------------------------------------


def _round(value: float) -> float:
    """Round to one decimal place (and turn -0.0 into 0.0)."""
    return round(value, 1) + 0.0


def _watts(value: float) -> str:
    """`300` for whole watts, `0.8` otherwise (to one decimal place, so 14.000001 is `14`)."""
    value = float(round(value, 1))
    return f"{value:.0f}" if value.is_integer() else f"{value:.1f}"


def _join_sentences(*parts: str) -> str:
    """Join fragments into sentences, adding full stops where missing."""
    sentences = []
    for part in parts:
        text = part.strip()
        if text:
            sentences.append(text if text.endswith((".", "!", "?")) else f"{text}.")
    return " ".join(sentences)
