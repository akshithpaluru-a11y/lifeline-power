"""Regression tests for the third review round.

Each section header names the problem it covers.
"""

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import anthropic
import httpx2
import pytest

from app.ai import MAX_IMAGE_BYTES, DeviceIdentifier, identify_from_catalog
from app.catalog import DEVICE_CATALOG
from app.errors import ApiError
from app.outage_engine import HelpTarget, TimelineContext, advance_state, build_milestones
from app.planner import BackupInput, DeviceInput, HouseholdFacts, build_plan, plugs_of
from tests.support import FakeClock, Home, device, generator, station

START = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
FACTS = HouseholdFacts()


def critical(devices: list[DeviceInput], backups: list[BackupInput]) -> float | None:
    return build_plan(devices, backups, FACTS, now=START).critical_runtime_minutes


def catalog_device(kind: str, **overrides: Any) -> DeviceInput:
    spec = DEVICE_CATALOG[kind]
    fields: dict[str, Any] = {
        "kind": kind,
        "draw_watts": spec.typical_draw_watts,
        "duty_cycle": spec.duty_cycle,
        "ac_powered": spec.ac_powered,
        "internal_battery_wh": spec.internal_battery_wh,
    }
    return device(kind, **(fields | overrides))


def milestone_at(outage: dict[str, Any], key: str) -> datetime:
    return datetime.fromisoformat(next(m for m in outage["milestones"] if m["key"] == key)["at"])


async def check_in(home: Home, **body: Any) -> dict[str, Any]:
    response = await home.client.post(
        f"{home.url}/outages/current/check-in", json={"ok": True, **body}, headers=home.owner
    )
    assert response.status_code == 200, response.text
    outage: dict[str, Any] = response.json()["outage"]
    return outage


# --- P0: a full reading from a device used now and then must not drain the shared battery ---


def test_full_reading_from_a_duty_cycled_device_takes_nothing_from_the_station() -> None:
    # A wheelchair's estimate is a daily average (15% of the time at 150 W). Reading 100% an
    # hour later most likely means it sat still, not that the station ran it.
    chair = device(
        "chair", life_sustaining=False, draw_watts=150, duty_cycle=0.15, internal_battery_wh=600
    )
    plan = build_plan([chair], [station()], FACTS, now=START)
    _, [new_station] = advance_state([chair], [station()], plan, 60, {"chair": 100})
    assert new_station.charge_percent == 100


def test_charge_a_duty_cycled_device_gained_still_comes_from_the_station() -> None:
    chair = device(
        "chair",
        life_sustaining=False,
        draw_watts=150,
        duty_cycle=0.15,
        internal_battery_wh=600,
        charge_percent=50,
    )
    plan = build_plan([chair], [station()], FACTS, now=START)
    [new_chair], [new_station] = advance_state([chair], [station()], plan, 60, {"chair": 60})
    assert new_chair.charge_percent == 60
    # 10% of 600 Wh x 0.9 usable = 54 Wh at the chair, 63.5 Wh from the station's battery.
    assert new_station.charge_percent == pytest.approx(100 * (1000 - 54 / 0.85) / 1000)


async def test_hourly_full_wheelchair_readings_leave_the_ventilator_countdown_steady(
    home: Home, clock: FakeClock
) -> None:
    vent = await home.add_device(kind="home_ventilator", label="Vent", spare_batteries_wh=0)
    chair = await home.add_device(kind="power_wheelchair", label="Chair")
    await home.add_backup(type="power_station", capacity_wh=1000)
    outage = await home.start_outage()
    start_runtime = outage["plan"]["critical_runtime_minutes"]

    for hour in range(1, 7):
        clock.advance(minutes=60)
        outage = await check_in(
            home,
            devices=[
                {"device_id": vent["id"], "charge_percent": 100},
                {"device_id": chair["id"], "charge_percent": 100},
            ],
        )
        assert outage["plan"]["critical_runtime_minutes"] == pytest.approx(
            start_runtime - 60 * hour, abs=1
        )


# --- P4: a check-in keeps the plugs where the family put them ------------------------------


async def test_check_in_without_readings_keeps_the_car_s_devices_and_the_countdown(
    home: Home, clock: FakeClock
) -> None:
    for kind in ("home_ventilator", "oxygen_concentrator_portable", "infusion_pump"):
        await home.add_device(kind=kind)
    await home.add_device(kind="suction_machine")
    car = await home.add_backup(type="car_inverter")
    outage = await home.start_outage()
    on_car = next(b for b in outage["plan"]["backups"] if b["backup_id"] == car["id"])
    depleted = milestone_at(outage, "depleted")

    for _ in range(4):
        clock.advance(minutes=60)
        outage = await check_in(home)
        again = next(b for b in outage["plan"]["backups"] if b["backup_id"] == car["id"])
        assert again["device_ids"] == on_car["device_ids"]
        assert abs((milestone_at(outage, "depleted") - depleted).total_seconds()) < 60


def test_a_device_whose_backup_ran_dry_moves_to_one_with_room() -> None:
    vent = device("vent", draw_watts=20, internal_battery_wh=95)
    o2 = device("o2", draw_watts=120)
    empty = station("empty", charge_percent=0)
    spare = station("spare", capacity_wh=500)
    plan = build_plan([vent, o2], [empty, spare], FACTS, now=START)
    keep = {"empty": ["o2"], "spare": ["vent"]}  # o2 was on the station that is now empty
    kept = build_plan([vent, o2], [empty, spare], FACTS, now=START, keep_plugs=keep)
    assert plugs_of(kept) == {"empty": [], "spare": ["vent", "o2"]}
    assert kept.critical_runtime_minutes == plan.critical_runtime_minutes


def test_a_kept_backup_that_can_no_longer_run_its_devices_is_rearranged() -> None:
    # Saver mode switched off mid-outage: the two devices no longer fit on the car together.
    a = device("a", draw_watts=100, internal_battery_wh=50)
    b = device("b", draw_watts=100)
    car = generator("car", type="car_inverter", max_output_watts=150)
    plan = build_plan([a, b], [car], FACTS, now=START, keep_plugs={"car": ["a", "b"]})
    assert plugs_of(plan) == {"car": ["b"]}


def test_plans_made_before_the_arrangement_was_recorded_choose_freely() -> None:
    plan = build_plan([device("a")], [station()], FACTS, now=START)
    old = plan.model_copy(
        update={"backups": [b.model_copy(update={"device_ids": None}) for b in plan.backups]}
    )
    assert plugs_of(plan) == {"s": ["a"]}
    assert plugs_of(old) is None


# --- P5: the plug search finds arrangements that power everyone, whatever the backup order --


def test_four_device_household_is_planned_the_same_whatever_the_backup_order() -> None:
    devices = [
        device("d0", draw_watts=150, ac_powered=False),
        device("d1", draw_watts=60, internal_battery_wh=95, ac_powered=False),
        device("d2", draw_watts=100, internal_battery_wh=50),
        device("d3", draw_watts=250),  # no battery; only b0 (300 W) can run it
    ]
    backups = [
        station("b0", type="ups", capacity_wh=150, max_output_watts=300),
        station("b1", type="ups", capacity_wh=150, max_output_watts=150),
        generator("b2", max_output_watts=150, fuel_hours=4),
    ]
    assert critical(devices, backups) == critical(devices, backups[::-1]) == 30.6


def test_no_life_sustaining_device_is_left_unpowered_when_a_plan_powers_everyone() -> None:
    devices = [
        device("d0", draw_watts=250, internal_battery_wh=200),
        device("d1", draw_watts=350, ac_powered=False),  # only b0 can run it, no battery
        device("d2", draw_watts=60, internal_battery_wh=10),
        device("d3", draw_watts=200, ac_powered=False),
        device("d4", draw_watts=100, internal_battery_wh=95),
    ]
    backups = [
        station("b0", capacity_wh=300, max_output_watts=500),
        generator("b1", type="car_inverter", max_output_watts=150, fuel_hours=10),
        station("b2", type="ups", capacity_wh=150, max_output_watts=300),
    ]
    runtime = critical(devices, backups)
    assert runtime is not None and runtime > 0


def test_catalog_household_powers_the_concentrator_whatever_the_backup_order() -> None:
    devices = [
        catalog_device("cpap_bipap"),
        catalog_device("suction_machine"),
        catalog_device("oxygen_concentrator_home", draw_watts=300),
        catalog_device("oxygen_concentrator_portable"),
        catalog_device("home_dialysis"),
    ]
    backups = [
        generator("car300", type="car_inverter", max_output_watts=300, fuel_hours=10),
        station("jackery300", capacity_wh=293, max_output_watts=300),
        generator("car150", type="car_inverter", max_output_watts=150, fuel_hours=10),
    ]
    assert critical(devices, backups) == critical(devices, backups[::-1]) == 74.7


def test_small_households_get_the_same_runtime_in_any_backup_order() -> None:
    rng = random.Random(7)
    kinds = [k for k in DEVICE_CATALOG if k != "custom"]
    for _ in range(60):
        devices = [catalog_device(kind, life_sustaining=True) for kind in rng.sample(kinds, k=4)]
        backups = [
            generator("car", type="car_inverter", max_output_watts=rng.choice([150, 300])),
            station("big", capacity_wh=rng.choice([300, 1000]), max_output_watts=300),
            station("ups", type="ups", capacity_wh=150, max_output_watts=rng.choice([150, 300])),
        ]
        runtimes = {
            critical(devices, order)
            for order in (backups, backups[::-1], backups[1:] + backups[:1])
        }
        assert len(runtimes) == 1, ([d.kind for d in devices], backups, runtimes)


# --- Integrations review: model figures, photo errors --------------------------------------


@pytest.mark.parametrize(
    ("text", "label", "confidence"),
    [
        ("Inogen One G4", "Inogen One G4", "medium"),
        ("Philips SimplyGo Mini", "Philips SimplyGo Mini", "medium"),
        ("Respironics SimplyGo Mini", "Philips SimplyGo Mini", "medium"),
        ("Philips SimplyGo", "Philips SimplyGo", "high"),
        ("Inogen Rove 6", "Inogen Rove 6", "high"),
    ],
)
def test_models_without_their_own_figures_are_not_high_confidence(
    text: str, label: str, confidence: str
) -> None:
    result = identify_from_catalog(text)
    assert (result.match.label, result.confidence) == (label, confidence)


def test_rove_6_uses_its_g5_battery_at_the_highest_setting() -> None:
    match = identify_from_catalog("Inogen Rove 6").match
    assert (match.draw_watts, match.internal_battery_wh) == (83, 92)


def _claude_rejected(message: str) -> anthropic.BadRequestError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(
        message, response=httpx2.Response(400, request=request), body=None
    )


async def test_a_request_error_that_is_not_about_the_photo_is_not_blamed_on_it() -> None:
    fake = SimpleNamespace(
        messages=SimpleNamespace(
            parse=AsyncMock(side_effect=_claude_rejected("max_tokens: must be at most 1024"))
        )
    )
    helper = DeviceIdentifier(fake, "claude-test-model")  # type: ignore[arg-type]
    with pytest.raises(ApiError) as caught:
        await helper.identify(None, b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", "image/jpeg")
    assert caught.value.status_code == 503


async def test_photos_over_the_limit_claude_accepts_are_refused_up_front() -> None:
    helper = DeviceIdentifier(None, "claude-test-model")
    big = b"\xff\xd8\xff\xe0" + bytes(MAX_IMAGE_BYTES)
    with pytest.raises(ApiError) as caught:
        await helper.identify("feeding pump", big, "image/jpeg")
    assert caught.value.status_code == 400
    assert MAX_IMAGE_BYTES * 4 / 3 < 5 * 1024 * 1024


# --- P1, P2, P3: alert wording -------------------------------------------------------------


def timeline_context(kinds: dict[str, str], plan_at: datetime = START) -> TimelineContext:
    return TimelineContext(
        source="manual",
        speed=1,
        started_at=START,
        plan_at=plan_at,
        last_check_in_at=plan_at,
        help_target=HelpTarget("County Hospital", 45),
        device_labels={device_id: f"Sam's {kind}" for device_id, kind in kinds.items()},
        device_kinds=kinds,
    )


@pytest.mark.parametrize(
    ("kind", "backup"),
    [
        ("enteral_feeding_pump", "gravity or syringe feeding"),
        ("home_ventilator", "manual resuscitation bag"),
        ("oxygen_concentrator_home", "oxygen cylinders"),
        ("suction_machine", "manual suction device"),
        ("home_dialysis", "dialysis center's emergency plan"),
        ("custom", "your care team's emergency backup plan"),
    ],
)
def test_emergency_backup_advice_fits_the_device(kind: str, backup: str) -> None:
    pump = device("pump", kind=kind, internal_battery_wh=0)
    plan = build_plan([pump], [], FACTS, now=START)
    milestones = build_milestones(plan, timeline_context({"pump": kind}))
    for key in ("power_lost", "leave_by"):
        detail = next(m for m in milestones if m.key == key).detail
        assert backup in detail, (key, detail)
        if kind != "home_ventilator":
            assert "resuscitation" not in detail


def test_an_announced_halfway_alert_stays_as_it_was_sent() -> None:
    vent = device("vent", kind="home_ventilator", draw_watts=20, internal_battery_wh=95)
    ctx = timeline_context({"vent": "home_ventilator"})
    first = build_milestones(build_plan([vent], [], FACTS, now=START), ctx)
    halfway = next(m for m in first if m.key == "halfway")
    sent = [m.model_copy(update={"passed": m.passed or m.key == "halfway"}) for m in first]

    # A check-in just after halfway finds the battery much lower than planned.
    later = halfway.at + timedelta(minutes=10)
    low = replace(vent, charge_percent=5)
    rebuilt = build_milestones(
        build_plan([low], [], FACTS, now=later),
        replace(ctx, plan_at=later, last_check_in_at=later),
        previous=sent,
    )
    assert (
        next(m for m in rebuilt if m.key == "halfway")
        == sent[[m.key for m in sent].index("halfway")]
    )


def test_conserve_steps_are_actions_after_one_care_team_caveat() -> None:
    o2 = device("o2", kind="oxygen_concentrator_home", draw_watts=300, saver_draw_watts=250)
    bipap = device("bipap", kind="cpap_bipap", draw_watts=60, saver_draw_watts=20, duty_cycle=1)
    plan = build_plan([o2, bipap], [station()], FACTS, now=START)
    ctx = timeline_context({"o2": "oxygen_concentrator_home", "bipap": "cpap_bipap"})
    detail = next(m for m in build_milestones(plan, ctx) if m.key == "conserve").detail
    assert detail.count("care team") == 1, detail
    assert "Only if your care team has already approved it:" in detail
    assert "switch Sam's oxygen_concentrator_home to a lower flow setting" in detail
