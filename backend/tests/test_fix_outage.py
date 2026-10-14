"""Regression tests for the outage review findings.

Each section header names the findings it covers: check-in battery estimates, the wording and
order of the countdown milestones, the help site that `leave_by` is timed on, missed-check-in
alerts, very long runtimes and caregiver acknowledgements.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.context import AppContext
from app.help_sites import haversine_km, travel_minutes
from app.outage_engine import (
    HelpTarget,
    TimelineContext,
    advance_state,
    apply_reports,
    build_milestones,
    mark_due,
    missed_check_in_alerts_covered,
)
from app.planner import HouseholdFacts, build_plan
from app.schemas import HelpSite, HelpSiteKind, Milestone, Plan
from app.ticker import OutageTicker
from tests.support import (
    FakeClock,
    Home,
    bearer,
    collect,
    device,
    generator,
    overpass_element,
    station,
)

START = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
FACTS = HouseholdFacts()
HOME_LAT, HOME_LON = 41.2565, -95.9345


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
    travel: float | None = 12, plan_at: datetime = START, labels: dict[str, str] | None = None
) -> TimelineContext:
    return TimelineContext(
        source="manual",
        speed=1,
        started_at=START,
        plan_at=plan_at,
        last_check_in_at=plan_at,
        help_target=HelpTarget("County Hospital", travel)
        if travel is not None
        else HelpTarget.from_sites([]),
        device_labels=labels or {"vent": "Dad's ventilator"},
    )


def by_key(milestones: list[Milestone], key: str) -> Milestone:
    return next(m for m in milestones if m.key == key)


def minutes_after_start(when: datetime) -> float:
    return (when - START).total_seconds() / 60


def milestone_at(outage: dict[str, Any], key: str) -> datetime:
    return datetime.fromisoformat(next(m for m in outage["milestones"] if m["key"] == key)["at"])


async def check_in(home: Home, **body: Any) -> dict[str, Any]:
    response = await home.client.post(
        f"{home.url}/outages/current/check-in", json={"ok": True, **body}, headers=home.owner
    )
    assert response.status_code == 200, response.text
    outage: dict[str, Any] = response.json()["outage"]
    return outage


# --- Finding 0: a device reading "100%" must not hide the drain on a shared battery ---------


async def test_hourly_full_readings_from_a_plugged_in_ventilator_count_down(
    home: Home, clock: FakeClock
) -> None:
    vent = await home.add_device(kind="home_ventilator", label="Vent", spare_batteries_wh=0)
    await home.add_backup(type="power_station", capacity_wh=1000)
    outage = await home.start_outage()
    # 95 Wh x 0.9 / 20 W = 256.5 min on its own battery, plus 1000 Wh x 0.85 / 20 W = 2550 min.
    assert outage["plan"]["critical_runtime_minutes"] == 2806.5
    depleted = milestone_at(outage, "depleted")

    for hour in range(1, 13):
        clock.advance(minutes=60)
        # Plugged into the station, the ventilator keeps its own battery full.
        outage = await check_in(home, devices=[{"device_id": vent["id"], "charge_percent": 100}])
        assert outage["plan"]["critical_runtime_minutes"] == pytest.approx(
            2806.5 - 60 * hour, abs=0.5
        )
        assert abs((milestone_at(outage, "depleted") - depleted).total_seconds()) < 60


def test_full_reading_takes_the_energy_from_the_device_s_station_allocation() -> None:
    vent = device("vent", draw_watts=20, internal_battery_wh=95, spare_batteries_wh=95)
    plan = build_plan([vent], [station()], FACTS, now=START)
    # Five hours plugged in: the station ran it (100 Wh at the device, 117.6 Wh from the
    # battery through the inverter), the internal battery and the spare are untouched.
    [new_vent], [new_station] = advance_state([vent], [station()], plan, 300, {"vent": 100})
    assert new_vent.charge_percent == 100
    assert new_vent.spare_batteries_wh == 95
    assert new_station.charge_percent == pytest.approx(100 * (1000 - 100 / 0.85) / 1000)


def test_full_reading_is_charged_to_a_station_the_plan_did_not_allocate() -> None:
    # The plan gives the whole station to the concentrator (the ventilator outlasts it on its
    # own battery), but families plug everything in: a full ventilator battery after an hour
    # means the station ran both.
    o2 = device("o2", draw_watts=350)
    vent = device("vent", draw_watts=20, internal_battery_wh=95)
    plan = build_plan([o2, vent], [station()], FACTS, now=START)
    assert next(p for p in plan.devices if p.device_id == "vent").allocations == []

    _, [new_station] = advance_state([o2, vent], [station()], plan, 60, {"vent": 100})
    assert new_station.charge_percent == pytest.approx(100 * (1000 - 370 / 0.85) / 1000)


def test_station_then_internal_battery_reading_matches_reality() -> None:
    vent = device("vent", draw_watts=20, internal_battery_wh=95)
    plan = build_plan([vent], [station()], FACTS, now=START)
    # Reality: 2550 min on the station until it was empty, then 51.3 min on the internal
    # battery, which now reads 80%. 80% of 256.5 min is left.
    devices, backups = advance_state([vent], [station()], plan, 2550 + 51.3, {"vent": 80})
    after = build_plan(devices, backups, FACTS, now=START)
    assert backups[0].charge_percent == pytest.approx(0, abs=1e-6)
    assert after.critical_runtime_minutes == pytest.approx(0.8 * 256.5, abs=0.2)


def test_reading_that_matches_the_estimate_changes_nothing() -> None:
    # Running on its own battery (not plugged in): the reading agrees with the estimate.
    vent = device("vent", draw_watts=20, internal_battery_wh=95)
    plan = build_plan([vent], [station()], FACTS, now=START)
    estimated = advance_state([vent], [station()], plan, 60)
    reading = estimated[0][0].charge_percent
    assert advance_state([vent], [station()], plan, 60, {"vent": reading}) == estimated
    lower = advance_state([vent], [station()], plan, 60, {"vent": reading - 10})
    assert lower[1] == estimated[1]  # a lower reading never gives energy back to the station


def test_reading_while_on_a_generator_is_not_charged_to_the_station() -> None:
    vent = device("vent", draw_watts=20, internal_battery_wh=95, charge_percent=80)
    backups = [generator(fuel_hours=2), station()]
    plan = build_plan([vent], backups, FACTS, now=START)
    # The generator ran it (and charged its battery) for the whole hour.
    devices, new_backups = advance_state([vent], backups, plan, 60, {"vent": 100})
    [new_vent], [_, new_station] = apply_reports(devices, new_backups, {"vent": 100}, {})
    assert new_vent.charge_percent == 100
    assert new_station.charge_percent == 100


# --- Finding 7: drain from the exact draw, not the plan's rounded figures -------------------


@pytest.mark.parametrize(("draw", "duty"), [(0.75, 1.0), (0.74, 1.0), (0.4, 0.1)])
def test_check_in_drain_uses_the_exact_draw(draw: float, duty: float) -> None:
    pump = device("pump", draw_watts=draw, duty_cycle=duty, internal_battery_wh=10)
    before = build_plan([pump], [], FACTS, now=START)
    later, _ = advance_state([pump], [], before, 360)
    after = build_plan(later, [], FACTS, now=START)
    assert before.critical_runtime_minutes is not None
    assert after.critical_runtime_minutes == pytest.approx(
        before.critical_runtime_minutes - 360, abs=0.2
    )


# --- Findings 1 and 41: halfway quotes the time actually left --------------------------------


def test_halfway_after_a_check_in_quotes_the_time_actually_left() -> None:
    first = build_milestones(critical_plan(1350), context())
    check_in_at = START + timedelta(minutes=300)
    rebuilt = build_milestones(critical_plan(67.5), context(plan_at=check_in_at), previous=first)
    halfway = by_key(rebuilt, "halfway")
    assert halfway.at == check_in_at
    assert not halfway.passed  # the ticker announces it now...
    assert halfway.detail.startswith("About 1 h 8 min left")  # ...with the real figure


def test_halfway_after_a_check_in_reporting_an_empty_station() -> None:
    first = build_milestones(critical_plan(3063), context(travel=None))
    check_in_at = START + timedelta(minutes=600)
    rebuilt = build_milestones(critical_plan(64), context(None, check_in_at), previous=first)
    assert by_key(rebuilt, "halfway").detail.startswith("About 1 h 4 min left")


def test_halfway_ahead_still_quotes_half_the_runtime() -> None:
    halfway = by_key(build_milestones(critical_plan(360), context()), "halfway")
    assert minutes_after_start(halfway.at) == 180
    assert halfway.detail.startswith("About 3 h left")


# --- Finding 2 and 47: no "get ready" after "leave now"; no "running on batteries" at 0 ------


def test_halfway_is_left_out_when_it_would_come_after_leave_by() -> None:
    # 72.9 min with the default 30 min travel: leave_by is due at 12.9, halfway at 36.5.
    milestones = build_milestones(critical_plan(72.9), context(travel=None))
    countdown = [m.key for m in milestones if m.severity != "info"]
    assert countdown == ["leave_by", "critical_low", "depleted"]


def test_announced_halfway_stays_on_the_timeline() -> None:
    first, _ = mark_due(build_milestones(critical_plan(360), context()), START + timedelta(hours=3))
    assert by_key(first, "halfway").passed
    # A check-in at 200 min finds only 40 min left: it's time to leave.
    check_in_at = START + timedelta(minutes=200)
    rebuilt = build_milestones(critical_plan(40), context(plan_at=check_in_at), previous=first)
    halfway = by_key(rebuilt, "halfway")
    assert halfway.passed
    assert halfway.detail.startswith("About 40 min left")


def test_no_power_at_all_says_the_device_has_stopped() -> None:
    milestones = build_milestones(critical_plan(0), context())
    power_lost = by_key(milestones, "power_lost")
    assert "running on batteries" not in power_lost.detail
    assert power_lost.detail.startswith("Dad's ventilator has no battery or backup power")
    assert "emergency backup" in power_lost.detail
    assert "call 911" in power_lost.detail
    assert "halfway" not in [m.key for m in milestones]


def test_power_lost_with_batteries_is_unchanged() -> None:
    power_lost = by_key(build_milestones(critical_plan(360), context()), "power_lost")
    assert power_lost.detail == "Devices are running on batteries and backup power now."


def test_start_milestones_keep_their_wording_after_a_check_in() -> None:
    first = build_milestones(critical_plan(360), context())
    later = START + timedelta(minutes=300)
    rebuilt = build_milestones(critical_plan(0), context(plan_at=later), previous=first)
    for key in ("power_lost", "conserve"):
        assert by_key(rebuilt, key) == by_key(first, key)


async def test_concentrator_without_backup_announces_that_it_stopped(
    home: Home, context: AppContext
) -> None:
    await home.add_device(kind="oxygen_concentrator_home", label="Mom's oxygen")
    with context.broker.subscribe(home.id) as events:
        await home.start_outage(source="charging_lost")
        [started] = await collect(events)
    power_lost = by_key(
        [Milestone.model_validate(m) for m in started.data["outage"]["milestones"]], "power_lost"
    )
    assert power_lost.detail.startswith("Mom's oxygen has no battery or backup power")


# --- Finding 44: when the battery can't last the drive, say so ------------------------------


def test_leave_by_says_call_911_when_the_battery_cannot_last_the_drive() -> None:
    milestones = build_milestones(critical_plan(19), context(travel=45))
    leave_by = by_key(milestones, "leave_by")
    assert leave_by.title == "Not enough battery to reach power"
    assert leave_by.severity == "critical"
    assert "Call 911 now" in leave_by.detail
    assert "Head to" not in leave_by.detail
    assert "County Hospital (45 min away)" in leave_by.detail
    assert "Call 911 now" in by_key(milestones, "critical_low").detail


def test_leave_now_when_the_drive_is_still_possible() -> None:
    leave_by = by_key(build_milestones(critical_plan(60), context(travel=45)), "leave_by")
    assert leave_by.title == "Leave for a powered location now"
    assert leave_by.severity == "warning"
    assert "Head to County Hospital (45 min away)" in leave_by.detail


def test_critical_low_does_not_let_a_family_on_the_road_wait() -> None:
    critical_low = by_key(build_milestones(critical_plan(360), context()), "critical_low")
    assert "Call 911 now unless you will reach power before then" in critical_low.detail


# --- Finding 40: oxygen setting tips always come with the care-team caveat first ------------


def test_conserve_puts_the_care_team_caveat_before_saver_tips() -> None:
    poc = device(
        "poc",
        kind="oxygen_concentrator_portable",
        draw_watts=30,
        saver_draw_watts=20,
        ac_powered=False,
        internal_battery_wh=92,
    )
    plan = build_plan([poc], [], FACTS, now=START)
    assert any(r.kind == "saver_mode" for r in plan.recommendations)
    conserve = by_key(build_milestones(plan, context(labels={"poc": "Inogen"})), "conserve")
    caveat = conserve.detail.index("Only if your care team has said it is safe")
    assert caveat < conserve.detail.index("lower pulse setting")


# --- Findings 24 and 43: leave_by is timed on a place likely to have power ------------------


def site(name: str, kind: HelpSiteKind, powered: bool, minutes: int) -> HelpSite:
    return HelpSite(
        id=name,
        name=name,
        kind=kind,
        lat=0,
        lon=0,
        distance_km=1,
        travel_minutes=minutes,
        likely_has_power=powered,
        address=None,
    )


def test_help_target_ignores_places_without_power() -> None:
    library = site("Town Library", "library", False, 7)
    assert HelpTarget.from_sites([library], searched_radius_km=15) == HelpTarget(None, 32)
    assert HelpTarget.from_sites([], searched_radius_km=50) == HelpTarget(None, 100)
    assert HelpTarget.from_sites([library]) == HelpTarget(None, 30)  # no search: unknown
    hospitals = [site("Far", "hospital", True, 40), site("Near", "fire_station", True, 20)]
    assert HelpTarget.from_sites([library, *hospitals]) == HelpTarget("Near", 20)


def overpass_by_radius(sites: dict[int, list[dict[str, Any]]]) -> Any:
    """An Overpass mock that answers according to the radius in the query."""

    def respond(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.content.decode())["data"][0]
        for radius_km, elements in sites.items():
            if f"around:{radius_km * 1000}," in query:
                return httpx.Response(200, json={"elements": elements})
        return httpx.Response(500)

    return respond


async def test_rural_home_searches_further_for_a_place_with_power(
    home: Home, external: respx.MockRouter
) -> None:
    library = overpass_element(1, "library", "Town Library", HOME_LAT + 0.027, HOME_LON)
    fire_lat = HOME_LAT + 0.2248  # about 25 km north
    fire = overpass_element(2, "fire_station", "County Fire Station", fire_lat, HOME_LON)
    external["overpass"].side_effect = overpass_by_radius({15: [library], 50: [library, fire]})
    await home.add_device(kind="home_ventilator", spare_batteries_wh=950)
    outage = await home.start_outage()

    expected = travel_minutes(haversine_km(HOME_LAT, HOME_LON, fire_lat, HOME_LON))
    leave_by = next(m for m in outage["milestones"] if m["key"] == "leave_by")
    assert f"County Fire Station ({expected} min away)" in leave_by["detail"]
    runtime = outage["plan"]["critical_runtime_minutes"]
    start = datetime.fromisoformat(outage["started_at"])
    assert (milestone_at(outage, "leave_by") - start).total_seconds() / 60 == pytest.approx(
        runtime - expected - 30, abs=0.1
    )


async def test_nothing_with_power_within_50_km_allows_the_drive_to_its_edge(
    home: Home, external: respx.MockRouter
) -> None:
    library = overpass_element(1, "library", "Town Library", HOME_LAT + 0.027, HOME_LON)
    external["overpass"].side_effect = overpass_by_radius({15: [library], 50: [library]})
    await home.add_device(kind="home_ventilator", spare_batteries_wh=950)
    outage = await home.start_outage()
    leave_by = next(m for m in outage["milestones"] if m["key"] == "leave_by")
    assert "Town Library" not in leave_by["detail"]
    assert "1 h 40 min" in leave_by["detail"]
    runtime = outage["plan"]["critical_runtime_minutes"]
    start = datetime.fromisoformat(outage["started_at"])
    assert (milestone_at(outage, "leave_by") - start).total_seconds() / 60 == pytest.approx(
        runtime - 100 - 30, abs=0.1
    )


async def test_map_service_down_still_assumes_30_minutes(home: Home) -> None:
    await home.add_device(kind="home_ventilator", spare_batteries_wh=950)
    outage = await home.start_outage()  # the Overpass mock answers 503
    runtime = outage["plan"]["critical_runtime_minutes"]
    start = datetime.fromisoformat(outage["started_at"])
    assert (milestone_at(outage, "leave_by") - start).total_seconds() / 60 == pytest.approx(
        runtime - 30 - 30, abs=0.1
    )


# --- Findings 3 and 37: one missed-check-in alert per 15 minutes, even after a gap ----------


def test_an_alert_covers_every_interval_that_has_passed() -> None:
    assert missed_check_in_alerts_covered(15.0) == 1
    assert missed_check_in_alerts_covered(29.9) == 1
    assert missed_check_in_alerts_covered(120.0) == 8


async def test_missed_check_in_after_a_ticker_gap_sends_one_alert(
    home: Home, clock: FakeClock, ticker: OutageTicker, context: AppContext
) -> None:
    await home.add_device(kind="home_ventilator", spare_batteries_wh=950)
    await home.start_outage()
    with context.broker.subscribe(home.id) as events:
        clock.advance(minutes=60 + 120)  # the server was down: two hours overdue
        for _ in range(8):
            await ticker.tick()
            clock.advance(seconds=2)
        missed = [e.data for e in await collect(events) if e.name == "missed_check_in"]
        assert [m["minutes_overdue"] for m in missed] == [120.0]

        clock.advance(minutes=15)
        await ticker.tick()
        missed = [e.data for e in await collect(events) if e.name == "missed_check_in"]
        assert [round(m["minutes_overdue"]) for m in missed] == [135]


# --- Findings 4, 8 and 38: absurdly long runtimes don't crash the outage --------------------


async def test_outage_with_an_absurdly_long_runtime_starts_and_checks_in(
    home: Home, clock: FakeClock
) -> None:
    await home.add_device(
        kind="custom",
        label="Pump",
        life_sustaining=True,
        draw_watts=0.001,
        duty_cycle=0.001,
        internal_battery_wh=100000,
        spare_batteries_wh=100000,
    )
    outage = await home.start_outage()
    assert outage["plan"]["critical_runtime_minutes"] > 1e10
    assert milestone_at(outage, "depleted").year < 2100
    clock.advance(minutes=60)
    await check_in(home)


# --- Finding 16: acknowledgements can't grow without bound ----------------------------------


async def test_acks_keep_each_caregiver_s_latest_five(home: Home, clock: FakeClock) -> None:
    await home.add_device(kind="home_ventilator")
    _, maya = await home.join("Maya")
    _, leo = await home.join("Leo")
    await home.start_outage()
    url = f"{home.url}/outages/current/ack"
    await home.client.post(url, json={"message": "On my way"}, headers=bearer(leo))
    for index in range(12):
        clock.advance(seconds=1)
        response = await home.client.post(
            url, json={"message": f"update {index}"}, headers=bearer(maya)
        )
        assert response.status_code == 200

    current = await home.client.get(f"{home.url}/outages/current", headers=home.owner)
    acks = current.json()["outage"]["acks"]
    assert [a["message"] for a in acks if a["name"] == "Maya"] == [
        f"update {index}" for index in range(7, 12)
    ]
    assert [a["message"] for a in acks if a["name"] == "Leo"] == ["On my way"]
