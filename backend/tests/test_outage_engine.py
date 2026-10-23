"""Outage timeline: milestone timing (including drill speed), check-ins and battery estimates."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.outage_engine import (
    BackupLevel,
    HelpTarget,
    TimelineContext,
    advance_state,
    apply_reports,
    build_milestones,
    mark_due,
    merge_with_household,
    missed_check_in_minutes,
    outage_minutes_between,
    to_real_time,
)
from app.planner import HouseholdFacts, build_plan
from app.schemas import HelpSite, Milestone, Plan
from tests.support import device, generator, station

START = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)


def critical_plan(runtime: float | None) -> Plan:
    """A minimal plan whose life-sustaining runtime is `runtime` minutes."""
    return Plan(
        computed_at=START,
        assumes_saver_mode=False,
        critical_runtime_minutes=runtime,
        bottleneck_device_id="vent" if runtime is not None else None,
        devices=[],
        backups=[],
        recommendations=[],
        assumptions=[],
    )


def context(
    speed: float = 1, travel: float | None = 12, plan_at: datetime = START
) -> TimelineContext:
    return TimelineContext(
        source="drill" if speed != 1 else "manual",
        speed=speed,
        started_at=START,
        plan_at=plan_at,
        last_check_in_at=plan_at,
        help_target=HelpTarget("Nebraska Medical Center", travel)
        if travel is not None
        else HelpTarget.from_sites([]),
        device_labels={"vent": "Dad's ventilator"},
    )


def offsets(milestones: list[Milestone]) -> dict[str, float]:
    """Minutes from the start of the outage to each milestone (real time)."""
    return {m.key: (m.at - START).total_seconds() / 60 for m in milestones}


def test_milestone_times_for_a_real_outage() -> None:
    milestones = build_milestones(critical_plan(360), context())
    assert offsets(milestones) == {
        "power_lost": 0,
        "conserve": 0,
        "check_in": 60,
        "halfway": 180,
        "leave_by": 360 - 12 - 30,
        "critical_low": 330,
        "depleted": 360,
    }
    assert [m.at for m in milestones] == sorted(m.at for m in milestones)
    leave_by = next(m for m in milestones if m.key == "leave_by")
    assert leave_by.severity == "warning"
    assert "Nebraska Medical Center (12 min away)" in leave_by.detail
    critical = next(m for m in milestones if m.key == "critical_low")
    assert critical.severity == "critical"
    assert "Call 911" in critical.detail


def test_drill_speed_compresses_every_milestone() -> None:
    # At speed 300 a 6-hour countdown takes 72 seconds.
    milestones = build_milestones(critical_plan(360), context(speed=300))
    real = {key: minutes * 60 for key, minutes in offsets(milestones).items()}
    assert real["depleted"] == pytest.approx(72)
    assert real["halfway"] == pytest.approx(36)
    assert real["check_in"] == pytest.approx(12)
    assert real["leave_by"] == pytest.approx((360 - 12 - 30) / 300 * 60)


def test_travel_defaults_to_30_minutes_when_unknown() -> None:
    milestones = build_milestones(critical_plan(360), context(travel=None))
    assert offsets(milestones)["leave_by"] == 360 - 30 - 30
    leave_by = next(m for m in milestones if m.key == "leave_by")
    assert "a hospital, fire station or other place with power" in leave_by.detail


def test_short_runtime_means_leave_now() -> None:
    # 20 minutes of power: leaving (12 min travel + 30 min buffer) is already overdue.
    milestones = build_milestones(critical_plan(20), context())
    leave_by = next(m for m in milestones if m.key == "leave_by")
    assert leave_by.at == START
    assert leave_by.title.endswith("now")
    critical = next(m for m in milestones if m.key == "critical_low")
    assert critical.at == START
    assert critical.title.startswith("Less than 30 minutes")


def test_no_life_sustaining_devices_means_no_countdown() -> None:
    keys = {m.key for m in build_milestones(critical_plan(None), context())}
    assert keys == {"power_lost", "conserve", "check_in"}


def test_only_start_milestones_are_passed_at_creation() -> None:
    milestones = build_milestones(critical_plan(360), context())
    assert {m.key for m in milestones if m.passed} == {"power_lost", "conserve"}


def test_rebuilding_keeps_passed_milestones_that_are_still_in_the_past() -> None:
    first = build_milestones(critical_plan(360), context())
    two_hours = START + timedelta(hours=2)
    first, _ = mark_due(first, two_hours)  # power_lost, conserve, check_in have passed
    # Check-in two hours in: 240 min left of the original 360.
    rebuilt = build_milestones(critical_plan(240), context(plan_at=two_hours), previous=first)
    passed = {m.key for m in rebuilt if m.passed}
    assert passed == {"power_lost", "conserve"}  # the next check-in is now an hour away
    assert offsets(rebuilt)["depleted"] == 360
    assert offsets(rebuilt)["halfway"] == 180  # halfway of the whole runtime, not of what's left


def test_mark_due_reports_newly_passed_milestones_once() -> None:
    milestones = build_milestones(critical_plan(360), context())
    updated, newly = mark_due(milestones, START + timedelta(minutes=181))
    assert [m.key for m in newly] == ["check_in", "halfway"]
    _, again = mark_due(updated, START + timedelta(minutes=182))
    assert again == []


def test_missed_check_in_alerts_every_15_minutes_after_due() -> None:
    milestones = build_milestones(critical_plan(360), context())
    due = START + timedelta(minutes=60)

    def overdue(minutes_after_due: float, sent: int) -> float | None:
        return missed_check_in_minutes(
            milestones, START, 1, due + timedelta(minutes=minutes_after_due), sent
        )

    assert overdue(14.9, 0) is None
    assert overdue(15, 0) == pytest.approx(15)
    assert overdue(20, 1) is None
    assert overdue(30, 1) == pytest.approx(30)


def test_missed_check_in_scales_with_drill_speed() -> None:
    milestones = build_milestones(critical_plan(360), context(speed=60))
    due = START + timedelta(minutes=1)  # 60 outage minutes at speed 60
    assert missed_check_in_minutes(milestones, START, 60, due + timedelta(seconds=14), 0) is None
    assert missed_check_in_minutes(
        milestones, START, 60, due + timedelta(seconds=15), 0
    ) == pytest.approx(15)


def test_time_conversions_round_trip() -> None:
    later = to_real_time(START, 360, 300)
    assert later - START == timedelta(seconds=72)
    assert outage_minutes_between(START, later, 300) == pytest.approx(360)


def test_help_target_prefers_places_with_power() -> None:
    def site(name: str, powered: bool, minutes: int) -> HelpSite:
        return HelpSite(
            id=name,
            name=name,
            kind="hospital" if powered else "library",
            lat=0,
            lon=0,
            distance_km=1,
            travel_minutes=minutes,
            likely_has_power=powered,
            address=None,
        )

    target = HelpTarget.from_sites([site("Library", False, 5), site("Hospital", True, 15)])
    assert target == HelpTarget("Hospital", 15)
    assert HelpTarget.from_sites([]).travel_minutes == 30


# --- Battery estimates at check-in ---------------------------------------------------------


def test_check_in_without_readings_keeps_the_countdown() -> None:
    devices = [
        device("o2", draw_watts=350),
        device("vent", draw_watts=20, internal_battery_wh=95, spare_batteries_wh=95),
    ]
    backups = [station(), generator(fuel_hours=2)]
    facts = HouseholdFacts()
    before = build_plan(devices, backups, facts, now=START)
    later_devices, later_backups = advance_state(devices, backups, before, 100)
    after = build_plan(later_devices, later_backups, facts, now=START)
    assert before.critical_runtime_minutes is not None
    assert after.critical_runtime_minutes == pytest.approx(
        before.critical_runtime_minutes - 100, abs=0.2
    )
    for old, new in zip(before.devices, after.devices, strict=True):
        assert new.total_minutes == pytest.approx(max(0, old.total_minutes - 100), abs=0.2)


def test_advance_state_drains_fuel_then_own_battery_then_shared() -> None:
    vent = device("vent", draw_watts=20, internal_battery_wh=95, spare_batteries_wh=95)
    gen = generator(fuel_hours=1)
    plan = build_plan([vent], [gen, station()], HouseholdFacts(), now=START)
    # 90 minutes: 60 on the generator, then 30 on the internal battery (10 Wh of 85.5 usable).
    [new_vent], [new_gen, new_station] = advance_state([vent], [gen, station()], plan, 90)
    assert new_gen.fuel_hours == 0
    assert new_vent.charge_percent == pytest.approx(100 * (85.5 - 10) / 85.5)
    assert new_vent.spare_batteries_wh == 95
    assert new_station.charge_percent == 100


def test_reports_override_estimates() -> None:
    devices, backups = [device("vent", internal_battery_wh=95)], [station(), generator()]
    new_devices, new_backups = apply_reports(
        devices,
        backups,
        {"vent": 40},
        {"s": BackupLevel(charge_percent=55), "g": BackupLevel(fuel_hours=3)},
    )
    assert new_devices[0].charge_percent == 40
    assert new_backups[0].charge_percent == 55
    assert new_backups[1].fuel_hours == 3


def test_merge_takes_settings_from_household_and_levels_from_outage() -> None:
    state_devices = [device("o2", charge_percent=30, spare_batteries_wh=10)]
    state_backups = [station(charge_percent=20)]
    household_devices = [
        device("o2", saver_enabled=True, spare_batteries_wh=95),
        device("new"),
    ]
    household_backups = [station(charge_percent=100), station("added")]
    devices, backups = merge_with_household(
        state_devices, state_backups, household_devices, household_backups
    )
    assert devices[0] == replace(household_devices[0], charge_percent=30, spare_batteries_wh=10)
    assert devices[1] == household_devices[1]
    assert backups[0].charge_percent == 20
    assert backups[1] == household_backups[1]
