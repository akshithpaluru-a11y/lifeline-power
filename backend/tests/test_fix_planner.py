"""Regression tests for the planner review findings (numbers worked out by hand)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.catalog import DEVICE_CATALOG
from app.outage_engine import advance_state
from app.planner import (
    BackupInput,
    DeviceInput,
    HouseholdFacts,
    _choice_limit,
    _unplugged_load,
    build_plan,
    effective_draw_watts,
)
from app.schemas import DevicePlan, Plan
from tests.support import Home, device, generator, station

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)


def plan(
    devices: list[DeviceInput], backups: list[BackupInput] | None = None, **kwargs: bool
) -> Plan:
    return build_plan(devices, backups or [], HouseholdFacts(), now=NOW, **kwargs)


def entry(result: Plan, device_id: str) -> DevicePlan:
    return next(d for d in result.devices if d.device_id == device_id)


def car(**overrides: Any) -> BackupInput:
    fields: dict[str, Any] = {"type": "car_inverter", "max_output_watts": 150.0, "fuel_hours": 10.0}
    return generator("car", **{**fields, **overrides})


def milestone_at(outage: dict[str, Any], key: str) -> datetime:
    return datetime.fromisoformat(next(m["at"] for m in outage["milestones"] if m["key"] == key))


# --- Finding 5: which devices a generator or car runs ---------------------------------------


async def test_car_runs_the_device_without_a_battery_first(
    client: httpx.AsyncClient, home: Home
) -> None:
    # Catalog defaults: ventilator 20 W (95 Wh: 256.5 min), portable O2 30 W DC (92 Wh:
    # 165.6 min), infusion pump 2 W (10 Wh: 270 min) and suction 100 W with no battery, on a
    # 150 W car. Plugging in the smallest devices first left suction with nothing (0 min).
    # Suction, O2 and ventilator fill the car exactly (150 W); the infusion pump runs on its own
    # battery, so the first life-sustaining device runs out after 270 minutes.
    for kind in ("home_ventilator", "oxygen_concentrator_portable", "infusion_pump"):
        await home.add_device(kind=kind)
    suction = await home.add_device(kind="suction_machine")
    await home.add_backup(type="car_inverter")

    result = (await client.get(f"{home.url}/plan", headers=home.owner)).json()["plan"]
    assert result["critical_runtime_minutes"] == 270.0
    suction_plan = next(d for d in result["devices"] if d["device_id"] == suction["id"])
    assert suction_plan["total_minutes"] == 600.0
    assert suction_plan["status"] == "ok"

    outage = await home.start_outage()
    start = datetime.fromisoformat(outage["started_at"])
    assert milestone_at(outage, "depleted") - start == timedelta(minutes=270)
    assert milestone_at(outage, "critical_low") - start == timedelta(minutes=240)


def test_generator_runs_the_device_that_has_no_battery() -> None:
    # 400 W: the 350 W concentrator (no battery) and the 60 W ventilator don't fit together.
    # The ventilator has 95 Wh of its own (85.5 Wh / 60 W = 85.5 min), so the generator goes to
    # the concentrator.
    result = plan(
        [device("o2", draw_watts=350), device("vent", draw_watts=60, internal_battery_wh=95)],
        [generator(max_output_watts=400)],
    )
    assert result.critical_runtime_minutes == 85.5
    assert entry(result, "o2").total_minutes == 480.0
    vent = entry(result, "vent")
    assert (vent.total_minutes, vent.status) == (85.5, "limited")
    assert "already fully loaded" in (vent.status_detail or "")


def test_no_backup_detail_does_not_contradict_itself() -> None:
    # Two 100 W life-sustaining devices and a 150 W car: one of them can't be plugged in.
    result = plan([device("a"), device("b")], [car()])
    left_out = next(d for d in result.devices if d.total_minutes == 0)
    assert left_out.status == "no_backup"
    detail = left_out.status_detail or ""
    assert "already fully loaded" in detail
    assert "no charged battery or usable backup" not in detail


def test_saver_gain_on_a_full_car_is_the_real_gain() -> None:
    # 400 W car. A: 300 W (saver 150 W) with 95 Wh = 17.1 min (34.2 min in saver mode);
    # B: 200 W and C: 190 W, no batteries. B + C fill the car either way, so A runs on its own
    # battery: saver mode adds 34.2 - 17.1 = 17.1 minutes of critical runtime.
    devices = [
        device("a", draw_watts=300, saver_draw_watts=150, internal_battery_wh=95),
        device("b", draw_watts=200),
        device("c", draw_watts=190),
    ]
    backups = [car(max_output_watts=400)]
    before = plan(devices, backups)
    assert before.critical_runtime_minutes == 17.1
    saver = next(r for r in before.recommendations if r.id == "saver_a")
    assert saver.gain_minutes == 17.1
    assert "adding about 17 min" in saver.detail

    after = plan([replace(devices[0], saver_enabled=True), *devices[1:]], backups)
    assert after.critical_runtime_minutes == 34.2


def test_fuel_shared_with_a_battery_goes_where_it_helps_most() -> None:
    # The DC-only pack can't run the AC device, so the car (which fits only one of the two
    # 100 W devices) must run the AC one and leave the DC one on the pack: 1000 Wh / 100 W.
    devices = [device("dc", draw_watts=100, ac_powered=False), device("ac", draw_watts=100)]
    result = plan(devices, [car(), station("pack", has_ac_outlet=False)])
    assert entry(result, "ac").total_minutes == 600.0
    assert entry(result, "dc").total_minutes == 600.0
    assert result.critical_runtime_minutes == 600.0


# --- Finding 6: a battery's combined output limit -------------------------------------------


def test_station_cannot_run_life_sustaining_devices_above_its_output() -> None:
    # 350 W + 20 W = 370 W on a 360 W station: it can run only one of them at a time, so one
    # life-sustaining device has no power at all.
    result = plan(
        [device("o2", draw_watts=350), device("vent", draw_watts=20)],
        [station(max_output_watts=360)],
    )
    assert result.critical_runtime_minutes == 0
    statuses = sorted(d.status for d in result.devices)
    assert statuses == ["no_backup", "ok"]
    stopped = next(d for d in result.devices if d.status == "no_backup")
    assert "already fully loaded" in (stopped.status_detail or "")
    warning = next(a for a in result.assumptions if "can't run all its devices" in a)
    assert "life-sustaining ones alone need 370 W" in warning


def test_station_output_goes_to_the_device_without_its_own_battery() -> None:
    # With its 95 Wh battery the ventilator lasts 256.5 min alone, so the station runs the
    # concentrator: 1000 Wh x 0.85 / 350 W = 145.7 min.
    result = plan(
        [device("o2", draw_watts=350), device("vent", draw_watts=20, internal_battery_wh=95)],
        [station(max_output_watts=360)],
    )
    assert result.critical_runtime_minutes == 145.7
    assert entry(result, "o2").status == "ok"
    vent = entry(result, "vent")
    assert (vent.total_minutes, vent.status) == (256.5, "limited")
    assert "already fully loaded" in (vent.status_detail or "")


def test_two_small_stations_share_out_their_outputs() -> None:
    # Two 300 W stations, three life-sustaining devices (250 W + 100 W + 40 W = 390 W): no one
    # station can run them all. The concentrator gets one station (850 Wh / 250 W = 204 min),
    # and suction and the ventilator share the other, so every device has power.
    devices = [
        device("o2", draw_watts=250),
        device("suction", draw_watts=100),
        device("vent", draw_watts=40),
    ]
    result = plan(devices, [station("a", max_output_watts=300), station("b", max_output_watts=300)])
    assert result.critical_runtime_minutes == 204.0
    assert [len(d.allocations) for d in result.devices] == [1, 1, 1]
    assert entry(result, "suction").total_minutes == entry(result, "vent").total_minutes == 364.3


def test_large_households_fall_back_to_the_greedy_choice_quickly() -> None:
    # 20 life-sustaining devices on 8 overloaded stations: too many ways to compare, so the
    # planner takes the greedy one (no device left without power while a station has room).
    loads = [
        _unplugged_load(device(f"d{i}", draw_watts=30 + 7 * i), saver_all=False) for i in range(20)
    ]
    usable = {f"s{i}": 500.0 for i in range(8)}
    assert _choice_limit(usable, loads) == 1
    assert _choice_limit({"s": 1000.0}, loads[:4]) == 16
    devices = [device(f"d{i}", draw_watts=30 + 7 * i) for i in range(20)]
    backups = [station(f"s{i}", max_output_watts=250 + 20 * i) for i in range(8)]
    result = plan(devices, backups)
    assert all(d.status != "no_backup" for d in result.devices)


def test_station_output_left_after_life_sustaining_devices_goes_to_others() -> None:
    # 100 W limit: the 20 W ventilator is plugged in first; the 90 W pump no longer fits.
    result = plan(
        [device("vent", draw_watts=20), device("pump", draw_watts=90, life_sustaining=False)],
        [station(max_output_watts=100)],
    )
    assert entry(result, "vent").total_minutes == 2550.0
    pump = entry(result, "pump")
    assert (pump.total_minutes, pump.status) == (0, "no_backup")
    assert "already fully loaded" in (pump.status_detail or "")
    assert any("plugs in life-sustaining devices first" in a for a in result.assumptions)


# --- Finding 9: why a non-critical device got no shared battery -----------------------------


def test_starved_detail_only_blames_life_sustaining_devices_when_they_took_the_battery() -> None:
    # The only life-sustaining device is an AC ventilator, which can't use the DC-only pack.
    # The fridge (no battery) gets the whole pack; the wheelchair's own 600 Wh outlasts it.
    devices = [
        device("vent", draw_watts=20, internal_battery_wh=95),
        device(
            "chair",
            kind="power_wheelchair",
            draw_watts=150,
            duty_cycle=0.15,
            internal_battery_wh=600,
            life_sustaining=False,
            ac_powered=False,
        ),
        device(
            "fridge",
            kind="medication_fridge",
            draw_watts=80,
            duty_cycle=0.35,
            life_sustaining=False,
            ac_powered=False,
        ),
    ]
    result = plan(devices, [station("pack", capacity_wh=200, has_ac_outlet=False)])
    assert entry(result, "fridge").total_minutes == pytest.approx(428.6)
    chair = entry(result, "chair")
    assert chair.total_minutes == 1440.0
    assert "life-sustaining" not in (chair.status_detail or "")


def test_starved_detail_kept_when_life_sustaining_devices_take_the_battery() -> None:
    result = plan(
        [
            device("vent", draw_watts=20),
            device("mon", draw_watts=5, internal_battery_wh=10, life_sustaining=False),
        ],
        [station()],
    )
    mon = entry(result, "mon")
    assert mon.status == "limited"
    assert mon.status_detail == "Shared backup power is saved for life-sustaining devices first."


# --- Finding 39: devices that run for hours at a stretch ------------------------------------


def bipap(**overrides: Any) -> DeviceInput:
    return device("bipap", kind="cpap_bipap", draw_watts=40, duty_cycle=0.35, **overrides)


def test_life_sustaining_bipap_is_counted_at_its_full_draw() -> None:
    # Catalog UPS: 150 Wh x 0.85 / 40 W = 191.25 min, not the 546.4 min its 14 W daily
    # average would give: an outage can start just as the BiPAP is switched on for the night.
    ups = station("ups", type="ups", capacity_wh=150, max_output_watts=900)
    result = plan([bipap()], [ups])
    assert result.critical_runtime_minutes == pytest.approx(191.25, abs=0.1)
    assert entry(result, "bipap").effective_draw_watts == 40
    note = next(a for a in result.assumptions if a.startswith("bipap is counted"))
    assert "full 40 W" in note and "about 14 W" in note


def test_non_critical_and_short_burst_devices_keep_their_average_draw() -> None:
    ups = station("ups", type="ups", capacity_wh=150, max_output_watts=900)
    # Not life-sustaining: the daily average (14 W) is fine, nothing counts down on it.
    assert entry(plan([bipap(life_sustaining=False)], [ups]), "bipap").total_minutes == 546.4
    # Suction is used in short bursts: 10% of 100 W. 127.5 Wh / 10 W = 765 min.
    suction = device("suction", kind="suction_machine", draw_watts=100, duty_cycle=0.1)
    assert plan([suction], [ups]).critical_runtime_minutes == 765.0
    # A custom device's pattern is unknown, so a life-sustaining one gets the cautious figure.
    custom = device("pump", draw_watts=50, duty_cycle=0.5)
    assert effective_draw_watts(custom) == 50
    assert effective_draw_watts(replace(custom, life_sustaining=False)) == 25


async def test_catalog_dialysis_countdown_uses_its_running_draw(
    client: httpx.AsyncClient, home: Home
) -> None:
    # A PD cycler (200 W) on the catalog power station: 850 Wh / 200 W = 255 min, where the
    # 80 W daily average claimed 637.5 min.
    await home.add_device(kind="home_dialysis")
    await home.add_backup(type="power_station")
    outage = await home.start_outage()
    assert outage["plan"]["critical_runtime_minutes"] == 255.0
    start = datetime.fromisoformat(outage["started_at"])
    assert milestone_at(outage, "depleted") - start == timedelta(minutes=255)


def test_check_in_estimate_matches_the_full_draw() -> None:
    # The check-in estimate must drain the BiPAP's own battery at the same 40 W the plan uses,
    # or the countdown would jump back up after every check-in.
    devices = [bipap(internal_battery_wh=50, spare_batteries_wh=50)]
    backups = [station()]
    before = plan(devices, backups)
    later_devices, later_backups = advance_state(devices, backups, before, 100)
    after = plan(later_devices, later_backups)
    assert before.critical_runtime_minutes is not None
    assert after.critical_runtime_minutes == pytest.approx(
        before.critical_runtime_minutes - 100, abs=0.2
    )


# --- Finding 45: home concentrator saver figure ---------------------------------------------


async def test_home_concentrator_has_no_assumed_saver_figure(
    client: httpx.AsyncClient, home: Home
) -> None:
    assert DEVICE_CATALOG["oxygen_concentrator_home"].saver_draw_watts is None
    # An EverFlo-style unit (350 W, no published lower-flow figure) gets no saver promise.
    o2 = await home.add_device(kind="oxygen_concentrator_home", label="EverFlo")
    assert o2["saver_draw_watts"] is None
    await home.add_backup(type="power_station")
    result = (await client.get(f"{home.url}/plan", headers=home.owner)).json()["plan"]
    assert not [r for r in result["recommendations"] if r["kind"] == "saver_mode"]
    # A family whose manual lists a lower-flow figure can still enter it.
    entered = await home.add_device(kind="oxygen_concentrator_home", saver_draw_watts=200)
    assert entered["saver_draw_watts"] == 200


def test_oxygen_saver_tips_defer_to_the_care_team() -> None:
    for kind in ("oxygen_concentrator_home", "oxygen_concentrator_portable"):
        spec = DEVICE_CATALOG[kind]
        assert spec.saver_action is not None and "care team" in spec.saver_action
        assert spec.saver_tip is not None
        assert "only if your care team has said it is safe" in spec.saver_tip.lower()
    poc = device(
        "poc",
        kind="oxygen_concentrator_portable",
        draw_watts=30,
        saver_draw_watts=20,
        internal_battery_wh=92,
        ac_powered=False,
    )
    rec = next(r for r in plan([poc]).recommendations if r.id == "saver_poc")
    assert rec.title.startswith("Ask your care team")
