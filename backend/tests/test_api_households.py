"""Households: create, read, update, delete, join codes, and the meta endpoints."""

import re

import httpx
import respx

from app.ids import JOIN_CODE_LENGTH
from tests.support import Home, bearer, create_home


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.json() == {"ok": True, "version": "1.0.0", "ai_enabled": False}


async def test_device_catalog_has_every_contract_kind(client: httpx.AsyncClient) -> None:
    devices = (await client.get("/api/catalog/devices")).json()["devices"]
    assert {d["kind"] for d in devices} == {
        "oxygen_concentrator_home",
        "oxygen_concentrator_portable",
        "home_ventilator",
        "cpap_bipap",
        "suction_machine",
        "enteral_feeding_pump",
        "infusion_pump",
        "power_wheelchair",
        "nebulizer",
        "medication_fridge",
        "home_dialysis",
        "pulse_oximeter_monitor",
        "pressure_mattress_pump",
        "custom",
    }
    vent = next(d for d in devices if d["kind"] == "home_ventilator")
    assert set(vent) >= {
        "name",
        "category",
        "icon",
        "life_sustaining_default",
        "typical_draw_watts",
        "saver_draw_watts",
        "saver_tip",
        "internal_battery_wh",
        "duty_cycle",
        "ac_powered",
        "description",
    }


async def test_backup_catalog(client: httpx.AsyncClient) -> None:
    backups = (await client.get("/api/catalog/backups")).json()["backups"]
    assert {b["type"]: b["icon"] for b in backups} == {
        "power_station": "power_station",
        "ups": "ups",
        "generator": "generator",
        "car_inverter": "car",
    }


async def test_create_household(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/households",
        json={
            "name": "  The Reyes home ",
            "patient_first_name": "Luis",
            "lat": 41.2565,
            "lon": -95.9345,
            "state": "ne",
            "utility_name": "OPPD",
        },
    )
    assert response.status_code == 201
    body = response.json()
    household = body["household"]
    assert body["owner_token"].startswith("llo_")
    assert household["id"].startswith("hh_")
    assert household["name"] == "The Reyes home"
    assert household["state"] == "NE"
    assert household["armed"] is True
    assert household["medical_program_registered"] is False
    assert re.fullmatch(rf"[A-Z2-9]{{{JOIN_CODE_LENGTH}}}", household["join_code"])
    assert household["devices"] == household["backups"] == household["caregivers"] == []
    assert household["created_at"] == "2026-10-20T18:00:00.000Z"


async def test_create_household_validation_errors_use_the_contract_shape(
    client: httpx.AsyncClient,
) -> None:
    cases = [
        ({"patient_first_name": "Luis"}, "name"),
        ({"name": "Home", "patient_first_name": "Luis", "lat": 41.0}, "lat and lon"),
        ({"name": "Home", "patient_first_name": "Luis", "state": "ZZ"}, "state"),
        ({"name": "", "patient_first_name": "Luis"}, "name"),
    ]
    for body, mentions in cases:
        response = await client.post("/api/households", json=body)
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "bad_request"
        assert mentions in error["message"]

    response = await client.post(
        "/api/households", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.json()["error"]["message"] == "The request body is not valid JSON."


async def test_create_household_geocodes_the_address(
    client: httpx.AsyncClient, external: respx.MockRouter
) -> None:
    external["census"].respond(
        200,
        json={
            "result": {
                "addressMatches": [
                    {
                        "coordinates": {"x": -95.93, "y": 41.26},
                        "addressComponents": {"state": "NE"},
                    }
                ]
            }
        },
    )
    home = await create_home(client, address="1200 Douglas St, Omaha, NE 68102")
    assert (home.data["lat"], home.data["lon"], home.data["state"]) == (41.26, -95.93, "NE")


async def test_create_household_without_reachable_geocoder_still_works(
    client: httpx.AsyncClient,
) -> None:
    home = await create_home(client, address="1200 Douglas St, Omaha, NE 68102")
    assert home.data["lat"] is None
    assert home.data["address"] == "1200 Douglas St, Omaha, NE 68102"


async def test_get_household_as_owner_and_caregiver(client: httpx.AsyncClient, home: Home) -> None:
    _, caregiver_token = await home.join()
    for token in (home.owner_token, caregiver_token):
        response = await client.get(home.url, headers=bearer(token))
        assert response.status_code == 200
        assert response.json()["household"]["id"] == home.id
    assert len(response.json()["household"]["caregivers"]) == 1


async def test_update_household(client: httpx.AsyncClient, home: Home) -> None:
    response = await client.patch(
        home.url,
        json={"name": "Grandpa's house", "armed": False, "medical_program_registered": True},
        headers=home.owner,
    )
    assert response.status_code == 200
    household = response.json()["household"]
    assert household["name"] == "Grandpa's house"
    assert household["armed"] is False
    assert household["medical_program_registered"] is True
    assert household["utility_name"] == "OPPD"  # untouched


async def test_update_household_can_clear_optional_fields_but_not_required_ones(
    client: httpx.AsyncClient, home: Home
) -> None:
    response = await client.patch(
        home.url, json={"utility_name": None, "lat": None, "lon": None}, headers=home.owner
    )
    household = response.json()["household"]
    assert household["utility_name"] is None
    assert household["lat"] is None

    response = await client.patch(home.url, json={"name": None}, headers=home.owner)
    assert response.status_code == 400
    response = await client.patch(home.url, json={"lat": 41.0}, headers=home.owner)
    assert response.status_code == 400


async def test_new_address_is_geocoded_and_replaces_old_coordinates(
    client: httpx.AsyncClient, home: Home, external: respx.MockRouter
) -> None:
    external["census"].respond(200, json={"result": {"addressMatches": []}})
    external["nominatim"].respond(
        200,
        json=[{"lat": "40.81", "lon": "-96.70", "address": {"ISO3166-2-lvl4": "US-NE"}}],
    )
    response = await client.patch(home.url, json={"address": "Lincoln, NE"}, headers=home.owner)
    household = response.json()["household"]
    assert (household["lat"], household["lon"]) == (40.81, -96.70)


async def test_delete_household(client: httpx.AsyncClient, home: Home) -> None:
    _, caregiver_token = await home.join()
    response = await client.delete(home.url, headers=home.owner)
    assert response.status_code == 204
    assert response.content == b""
    for token in (home.owner_token, caregiver_token):
        response = await client.get(home.url, headers=bearer(token))
        assert response.status_code == 401


async def test_rotate_join_code(client: httpx.AsyncClient, home: Home) -> None:
    old_code = home.join_code
    response = await client.post(f"{home.url}/join-code/rotate", headers=home.owner)
    assert response.status_code == 200
    new_code = response.json()["join_code"]
    assert new_code != old_code

    response = await client.post(
        "/api/caregivers/join", json={"join_code": old_code, "name": "Maya"}
    )
    assert response.status_code == 404
    response = await client.post(
        "/api/caregivers/join", json={"join_code": new_code, "name": "Maya"}
    )
    assert response.status_code == 201


async def test_join_code_is_forgiving_about_case_and_dashes(
    client: httpx.AsyncClient, home: Home
) -> None:
    code = f"{home.join_code[:3].lower()}-{home.join_code[3:]}"
    response = await client.post(
        "/api/caregivers/join",
        json={"join_code": code, "name": "Maya", "relationship": "Granddaughter"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["caregiver"]["relationship"] == "Granddaughter"
    assert body["caregiver_token"].startswith("llc_")
    assert body["household"]["caregivers"][0]["name"] == "Maya"


async def test_remove_caregiver(client: httpx.AsyncClient, home: Home) -> None:
    caregiver, token = await home.join()
    response = await client.delete(f"{home.url}/caregivers/{caregiver['id']}", headers=home.owner)
    assert response.status_code == 204
    assert (await client.get(home.url, headers=bearer(token))).status_code == 401
    response = await client.delete(f"{home.url}/caregivers/{caregiver['id']}", headers=home.owner)
    assert response.status_code == 404


async def test_unknown_route_uses_contract_error_shape(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/nope")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "No such endpoint."}}


async def test_cors_allows_any_origin(client: httpx.AsyncClient) -> None:
    response = await client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers["access-control-allow-origin"] == "*"
