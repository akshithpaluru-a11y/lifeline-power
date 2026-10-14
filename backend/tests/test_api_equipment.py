"""Devices and backups: catalog defaults, overrides, validation, and the plan endpoint."""

import httpx

from app.services.households import MAX_BACKUPS
from tests.support import Home, bearer


async def test_add_device_fills_in_catalog_defaults(home: Home) -> None:
    device = await home.add_device(kind="home_ventilator")
    assert device["id"].startswith("dv_")
    assert device["label"] == "Home ventilator"
    assert device["icon"] == "ventilator"
    assert device["life_sustaining"] is True
    assert device["draw_watts"] == 20
    assert device["internal_battery_wh"] == 95
    assert device["spare_batteries_wh"] == 0
    assert device["charge_percent"] == 100
    assert device["saver_enabled"] is False
    assert device["notes"] == ""


async def test_add_device_overrides(home: Home) -> None:
    device = await home.add_device(
        kind="cpap_bipap",
        label="Dad's CPAP",
        life_sustaining=True,
        draw_watts=55,
        saver_draw_watts=15,
        saver_enabled=True,
        internal_battery_wh=0,
        notes="Humidifier off in outages",
    )
    assert device["label"] == "Dad's CPAP"
    assert device["life_sustaining"] is True
    assert (device["draw_watts"], device["saver_draw_watts"]) == (55, 15)
    assert device["duty_cycle"] == 0.35  # from the catalog


async def test_catalog_saver_figure_is_dropped_when_it_no_longer_fits(home: Home) -> None:
    # The catalog's CPAP saver figure is 10 W; this one only draws 8 W.
    device = await home.add_device(kind="cpap_bipap", draw_watts=8)
    assert device["saver_draw_watts"] is None


async def test_device_validation(client: httpx.AsyncClient, home: Home) -> None:
    bad_bodies = [
        {"kind": "toaster"},
        {"kind": "custom", "draw_watts": 0},
        {"kind": "custom", "duty_cycle": 1.5},
        {"kind": "custom", "charge_percent": 101},
        {"kind": "custom", "draw_watts": 50, "saver_draw_watts": 60},
        {"kind": "custom", "label": None},
    ]
    for body in bad_bodies:
        response = await client.post(f"{home.url}/devices", json=body, headers=home.owner)
        assert response.status_code == 400, body
        assert response.json()["error"]["code"] == "bad_request"


async def test_update_device(client: httpx.AsyncClient, home: Home) -> None:
    device = await home.add_device(kind="cpap_bipap")
    response = await client.patch(
        f"{home.url}/devices/{device['id']}",
        json={"saver_enabled": True, "notes": None, "kind": "nebulizer", "id": "x"},
        headers=home.owner,
    )
    assert response.status_code == 200
    updated = response.json()["device"]
    assert updated["saver_enabled"] is True
    assert updated["notes"] == ""
    assert updated["kind"] == "cpap_bipap"  # kind and id can't be changed
    assert updated["id"] == device["id"]

    response = await client.patch(
        f"{home.url}/devices/{device['id']}", json={"draw_watts": -1}, headers=home.owner
    )
    assert response.status_code == 400


async def test_device_not_found(client: httpx.AsyncClient, home: Home) -> None:
    response = await client.patch(f"{home.url}/devices/dv_nope", json={}, headers=home.owner)
    assert response.status_code == 404
    assert response.json()["error"] == {"code": "not_found", "message": "Device not found."}


async def test_remove_device(client: httpx.AsyncClient, home: Home) -> None:
    device = await home.add_device(kind="custom")
    response = await client.delete(f"{home.url}/devices/{device['id']}", headers=home.owner)
    assert response.status_code == 204
    household = (await client.get(home.url, headers=home.owner)).json()["household"]
    assert household["devices"] == []


async def test_add_backup_defaults_by_type(home: Home) -> None:
    station = await home.add_backup(type="power_station", label="Jackery 1000")
    assert station["id"].startswith("bk_")
    assert (station["capacity_wh"], station["charge_percent"], station["fuel_hours"]) == (
        1000,
        100,
        None,
    )
    assert station["has_ac_outlet"] is True

    generator = await home.add_backup(type="generator")
    assert generator["label"] == "Portable generator"
    assert (generator["capacity_wh"], generator["charge_percent"]) == (None, None)
    assert generator["fuel_hours"] == 8
    assert generator["max_output_watts"] == 1800


async def test_backup_figures_must_suit_the_type(client: httpx.AsyncClient, home: Home) -> None:
    for body in (
        {"type": "generator", "capacity_wh": 500},
        {"type": "car_inverter", "charge_percent": 50},
        {"type": "ups", "fuel_hours": 3},
    ):
        response = await client.post(f"{home.url}/backups", json=body, headers=home.owner)
        assert response.status_code == 400, body


async def test_changing_backup_type_resets_figures(client: httpx.AsyncClient, home: Home) -> None:
    backup = await home.add_backup(type="ups")
    response = await client.patch(
        f"{home.url}/backups/{backup['id']}", json={"type": "generator"}, headers=home.owner
    )
    assert response.status_code == 200
    changed = response.json()["backup"]
    assert changed["type"] == "generator"
    assert changed["label"] == "Portable generator"
    assert (changed["capacity_wh"], changed["charge_percent"], changed["fuel_hours"]) == (
        None,
        None,
        8,
    )
    assert changed["max_output_watts"] == 1800


async def test_update_backup_charge(client: httpx.AsyncClient, home: Home) -> None:
    backup = await home.add_backup(type="power_station")
    response = await client.patch(
        f"{home.url}/backups/{backup['id']}", json={"charge_percent": 40}, headers=home.owner
    )
    assert response.json()["backup"]["charge_percent"] == 40
    response = await client.delete(f"{home.url}/backups/{backup['id']}", headers=home.owner)
    assert response.status_code == 204
    response = await client.delete(f"{home.url}/backups/{backup['id']}", headers=home.owner)
    assert response.status_code == 404


async def test_backup_limit(client: httpx.AsyncClient, home: Home) -> None:
    for _ in range(MAX_BACKUPS):
        await home.add_backup(type="ups")
    response = await client.post(f"{home.url}/backups", json={"type": "ups"}, headers=home.owner)
    assert response.status_code == 409


async def test_caregiver_cannot_change_equipment(client: httpx.AsyncClient, home: Home) -> None:
    _, token = await home.join()
    response = await client.post(
        f"{home.url}/devices", json={"kind": "custom"}, headers=bearer(token)
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Only the household's owner can do this."


async def test_plan_endpoint(client: httpx.AsyncClient, home: Home) -> None:
    # The catalog has no saver figure for concentrators; this one's manual lists 250 W.
    o2 = await home.add_device(kind="oxygen_concentrator_home", saver_draw_watts=250)
    station = await home.add_backup(type="power_station")
    _, token = await home.join()

    response = await client.get(f"{home.url}/plan", headers=bearer(token))
    assert response.status_code == 200
    plan = response.json()["plan"]
    # 1000 Wh x 0.85 / 350 W x 60 = 145.7 min
    assert plan["critical_runtime_minutes"] == 145.7
    assert plan["bottleneck_device_id"] == o2["id"]
    assert plan["devices"][0]["allocations"][0]["backup_id"] == station["id"]
    assert plan["assumes_saver_mode"] is False
    assert plan["computed_at"] == "2026-10-20T18:00:00.000Z"

    response = await client.get(f"{home.url}/plan?saver=true", headers=home.owner)
    plan = response.json()["plan"]
    assert plan["assumes_saver_mode"] is True
    assert plan["critical_runtime_minutes"] == 204.0  # at 250 W


async def test_empty_household_plan(client: httpx.AsyncClient, home: Home) -> None:
    plan = (await client.get(f"{home.url}/plan", headers=home.owner)).json()["plan"]
    assert plan["critical_runtime_minutes"] is None
    assert plan["devices"] == []
    assert any(r["id"] == "invite_caregiver" for r in plan["recommendations"])
