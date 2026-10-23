"""Planner math, checked against numbers worked out by hand."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.planner import (
    BackupInput,
    DeviceInput,
    HouseholdFacts,
    build_plan,
    effective_draw_watts,
    own_battery_wh,
)
from app.schemas import Plan
from tests.support import device, generator, station

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)


def plan(
    devices: list[DeviceInput],
    backups: list[BackupInput] | None = None,
    facts: HouseholdFacts | None = None,
    saver_preview: bool = False,
) -> Plan:
    return build_plan(
        devices, backups or [], facts or HouseholdFacts(), now=NOW, saver_preview=saver_preview
    )


def minutes(result: Plan, device_id: str) -> float:
    return next(d.total_minutes for d in result.devices if d.device_id == device_id)


# --- Rule 1 and 2: draw and the device's own batteries --------------------------------------


def test_effective_draw_uses_saver_only_when_enabled_and_lower() -> None:
    # Not life-sustaining: a life-sustaining one is counted at its full draw (test_fix_planner).
    cpap = device(draw_watts=40, saver_draw_watts=10, duty_cycle=0.35, life_sustaining=False)
    assert effective_draw_watts(cpap) == pytest.approx(14.0)
    assert effective_draw_watts(replace(cpap, saver_enabled=True)) == pytest.approx(3.5)
    assert effective_draw_watts(cpap, saver_all=True) == pytest.approx(3.5)
    # A "saver" figure that isn't lower is ignored.
    odd = device(draw_watts=40, saver_draw_watts=60, saver_enabled=True)
    assert effective_draw_watts(odd) == 40


def test_own_battery_counts_charge_and_90_percent_usable() -> None:
    # (95 Wh x 50% + 95 Wh spare) x 0.9 = 128.25 Wh
    vent = device(internal_battery_wh=95, spare_batteries_wh=95, charge_percent=50)
    assert own_battery_wh(vent) == pytest.approx(128.25)


def test_device_on_its_own_battery() -> None:
    # 95 Wh x 0.9 = 85.5 Wh at 20 W -> 256.5 min
    result = plan([device("vent", draw_watts=20, internal_battery_wh=95)])
    entry = result.devices[0]
    assert entry.internal_minutes == 256.5
    assert entry.total_minutes == 256.5
    assert entry.status == "ok"
    assert result.critical_runtime_minutes == 256.5
    assert result.bottleneck_device_id == "vent"


# --- Rule 3: shared batteries ---------------------------------------------------------------


def test_ac_device_loses_15_percent_to_the_inverter() -> None:
    # 1000 Wh x 0.85 / 350 W x 60 = 145.71 min
    result = plan([device("o2", draw_watts=350)], [station()])
    assert minutes(result, "o2") == 145.7
    assert result.devices[0].allocations[0].energy_wh == 1000.0
    assert result.backups[0].unused_wh == 0.0


def test_dc_device_has_no_inverter_loss() -> None:
    # 1000 Wh / 30 W x 60 = 2000 min
    result = plan([device("poc", draw_watts=30, ac_powered=False)], [station()])
    assert minutes(result, "poc") == 2000.0


def test_water_filling_levels_critical_devices_together() -> None:
    # Both drink from one station until they run out at the same moment:
    # 1000 Wh x 0.85 / (350 W + 20 W) x 60 = 137.84 min each.
    result = plan([device("o2", draw_watts=350), device("vent", draw_watts=20)], [station()])
    assert minutes(result, "o2") == 137.8
    assert minutes(result, "vent") == 137.8
    assert result.critical_runtime_minutes == 137.8


def test_water_filling_tops_up_the_lowest_device_first() -> None:
    # The ventilator's own battery (256.5 min) outlasts what the station gives the
    # concentrator (145.7 min), so the concentrator gets all of it.
    result = plan(
        [device("o2", draw_watts=350), device("vent", draw_watts=20, internal_battery_wh=95)],
        [station()],
    )
    assert minutes(result, "o2") == 145.7
    assert minutes(result, "vent") == 256.5
    assert result.bottleneck_device_id == "o2"


def test_compatibility_constraints_route_energy_where_it_can_go() -> None:
    # The DC-only station B can't run the AC concentrator, so the concentrator gets all of A
    # (145.7 min) and the DC device takes B: 500 Wh / 30 W x 60 = 1000 min, on top of its own
    # 92 Wh x 0.9 = 82.8 Wh (165.6 min). A proportional split would get this wrong.
    result = plan(
        [
            device("o2", draw_watts=350),
            device("poc", draw_watts=30, ac_powered=False, internal_battery_wh=92),
        ],
        [station("A"), station("B", capacity_wh=500, has_ac_outlet=False)],
    )
    assert minutes(result, "o2") == 145.7
    assert minutes(result, "poc") == 1165.6
    allocations = {
        d.device_id: {a.backup_id: a.energy_wh for a in d.allocations} for d in result.devices
    }
    assert allocations == {"o2": {"A": 1000.0}, "poc": {"B": 500.0}}


def test_backup_without_ac_outlet_cannot_run_ac_device() -> None:
    result = plan([device("o2", draw_watts=350)], [station(has_ac_outlet=False)])
    entry = result.devices[0]
    assert entry.status == "no_backup"
    assert entry.total_minutes == 0
    assert "no AC outlet" in (entry.status_detail or "")


def test_device_above_max_output_cannot_use_backup() -> None:
    result = plan([device("o2", draw_watts=350)], [station("ups", max_output_watts=300)])
    entry = result.devices[0]
    assert entry.status == "no_backup"
    assert "limit 300 W" in (entry.status_detail or "")


def test_non_critical_devices_get_only_what_is_left() -> None:
    # The ventilator alone could use all 1000 Wh (2550 min), so the fridge gets nothing.
    result = plan(
        [
            device("vent", draw_watts=20),
            device("fridge", draw_watts=80, duty_cycle=0.35, life_sustaining=False),
        ],
        [station()],
    )
    assert minutes(result, "vent") == 2550.0
    fridge = next(d for d in result.devices if d.device_id == "fridge")
    assert fridge.total_minutes == 0
    assert fridge.status == "no_backup"
    assert "Life-sustaining devices need all" in (fridge.status_detail or "")


def test_non_critical_devices_share_battery_when_no_critical_device_can_use_it() -> None:
    # The concentrator draws more than the small UPS can deliver, so the non-critical monitor
    # gets all of it: 150 Wh x 0.85 / 5 W x 60 = 1530 min.
    result = plan(
        [device("o2", draw_watts=350), device("mon", draw_watts=5, life_sustaining=False)],
        [station("ups", type="ups", capacity_wh=150, max_output_watts=300)],
    )
    assert minutes(result, "mon") == 1530.0
    assert minutes(result, "o2") == 0


def test_no_backup_device() -> None:
    result = plan([device("o2", draw_watts=350)])
    entry = result.devices[0]
    assert entry.status == "no_backup"
    assert entry.total_minutes == 0
    assert result.critical_runtime_minutes == 0


def test_no_life_sustaining_devices_means_no_critical_runtime() -> None:
    result = plan([device("fridge", life_sustaining=False)], [station()])
    assert result.critical_runtime_minutes is None
    assert result.bottleneck_device_id is None


def test_partly_charged_station() -> None:
    # 1000 Wh x 50% x 0.85 / 350 W x 60 = 72.86 min
    result = plan([device("o2", draw_watts=350)], [station(charge_percent=50)])
    assert minutes(result, "o2") == 72.9


# --- Rule 4: generators and car inverters ----------------------------------------------------


def test_generator_adds_fuel_hours_to_every_device_it_runs() -> None:
    result = plan(
        [device("o2", draw_watts=350), device("vent", draw_watts=20, internal_battery_wh=95)],
        [generator()],
    )
    assert minutes(result, "o2") == 480.0
    assert minutes(result, "vent") == 256.5 + 480.0
    gen = result.backups[0]
    assert gen.fuel_minutes == 480.0
    # Energy delivered over 8 h: (350 W + 20 W) x 8 h = 2960 Wh
    assert gen.usable_wh == 2960.0
    assert result.recommendations[0].id == "co_safety"
    assert result.recommendations[0].kind == "safety"
    assert any("one tank of fuel" in a for a in result.assumptions)


def test_generator_output_limit_runs_life_sustaining_devices_first() -> None:
    # 400 W of output: the life-sustaining concentrator (350 W) fits, the heater-sized
    # non-critical device (100 W) no longer does.
    result = plan(
        [device("o2", draw_watts=350), device("pump", draw_watts=100, life_sustaining=False)],
        [generator(max_output_watts=400)],
    )
    assert minutes(result, "o2") == 480.0
    pump = next(d for d in result.devices if d.device_id == "pump")
    assert pump.total_minutes == 0
    assert "already fully loaded" in (pump.status_detail or "")


def test_car_inverter_only_runs_small_devices() -> None:
    car = generator("car", type="car_inverter", max_output_watts=150, fuel_hours=10)
    result = plan([device("o2", draw_watts=350), device("vent", draw_watts=20)], [car])
    assert minutes(result, "vent") == 600.0
    assert minutes(result, "o2") == 0


# --- Recommendations ------------------------------------------------------------------------


def test_saver_recommendation_reports_real_gain() -> None:
    # Saver: 1000 x 0.85 / 250 x 60 = 204.0 min, versus 145.7 now: +58.3 min.
    o2 = device("o2", kind="oxygen_concentrator_home", draw_watts=350, saver_draw_watts=250)
    result = plan([o2], [station()])
    saver = next(r for r in result.recommendations if r.id == "saver_o2")
    assert saver.kind == "saver_mode"
    assert saver.gain_minutes == 58.3
    assert "350 W to 250 W" in saver.detail


def test_saver_preview_assumes_every_saver_setting() -> None:
    o2 = device("o2", draw_watts=350, saver_draw_watts=250)
    result = plan([o2], [station()], saver_preview=True)
    assert result.assumes_saver_mode
    assert minutes(result, "o2") == 204.0
    assert not any(r.kind == "saver_mode" for r in result.recommendations)


def test_saver_on_non_critical_device_has_no_critical_gain() -> None:
    cpap = device(
        "cpap",
        kind="cpap_bipap",
        draw_watts=40,
        saver_draw_watts=10,
        life_sustaining=False,
        duty_cycle=0.35,
    )
    result = plan([cpap], [station()])
    saver = next(r for r in result.recommendations if r.id == "saver_cpap")
    assert saver.gain_minutes is None
    assert "runs about" in saver.detail


def test_add_power_station_gain_is_the_rerun_difference() -> None:
    # A second 1000 Wh station doubles the concentrator's 145.7 min: +145.7 min.
    result = plan([device("o2", draw_watts=350)], [station()])
    rec = next(r for r in result.recommendations if r.id == "add_power_station")
    assert rec.gain_minutes == 145.7


def test_charge_recommendation_for_partly_charged_batteries() -> None:
    result = plan([device("o2", draw_watts=350)], [station(label="Jackery", charge_percent=0)])
    rec = next(r for r in result.recommendations if r.id == "charge_batteries")
    assert rec.gain_minutes == 145.7
    assert "Jackery is at 0%" in rec.detail


def test_recommendations_are_ordered_safety_then_gain_then_preparation() -> None:
    o2 = device("o2", kind="oxygen_concentrator_home", draw_watts=350, saver_draw_watts=250)
    facts = HouseholdFacts(
        medical_program_registered=False,
        caregiver_count=0,
        has_location=False,
        utility_name="OPPD",
        state="NE",
    )
    result = plan([o2], [station(charge_percent=80), generator()], facts)
    ids = [r.id for r in result.recommendations]
    assert ids[0] == "co_safety"
    gains = [r.gain_minutes for r in result.recommendations[1:] if r.gain_minutes is not None]
    assert gains == sorted(gains, reverse=True)
    assert ids[-3:] == ["register_program", "invite_caregiver", "set_location"]
    register = result.recommendations[-3]
    assert "Life-Sustaining Equipment" in register.title  # OPPD's real program


def test_registered_household_with_caregiver_and_location_gets_no_preparation_steps() -> None:
    result = plan([device("o2")], [station()], HouseholdFacts())
    assert not {"register_program", "invite_caregiver", "set_location"} & {
        r.id for r in result.recommendations
    }


def test_assumptions_are_plain_english() -> None:
    result = plan([device("o2", draw_watts=350)], [station()])
    assert any("15%" in a for a in result.assumptions)
    assert result.assumptions[-1].startswith("These are estimates")


def test_overload_warning_when_devices_together_exceed_output() -> None:
    result = plan(
        [device("a", draw_watts=300), device("b", draw_watts=300)],
        [station(max_output_watts=500)],
    )
    assert any("can't run all its devices at once" in a for a in result.assumptions)
