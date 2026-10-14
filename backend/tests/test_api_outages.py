"""Outages: start, drills and speed, check-ins, caregiver acks, end and history, plus events."""

import asyncio
from datetime import datetime
from typing import Any

import httpx
import pytest
import respx

from app.context import AppContext
from app.ticker import OutageTicker
from tests.support import FakeClock, Home, bearer, collect, overpass_element


def at(outage: dict[str, Any], key: str) -> datetime:
    milestone = next(m for m in outage["milestones"] if m["key"] == key)
    return datetime.fromisoformat(milestone["at"])


@pytest.fixture
async def equipped(home: Home) -> Home:
    """Omaha household: a concentrator (critical) on a 1000 Wh power station: 145.7 minutes."""
    await home.add_device(kind="oxygen_concentrator_home", label="Oxygen")
    await home.add_backup(type="power_station")
    return home


async def test_start_outage(client: httpx.AsyncClient, equipped: Home, context: AppContext) -> None:
    with context.broker.subscribe(equipped.id) as events:
        response = await client.post(
            f"{equipped.url}/outages", json={"source": "manual"}, headers=equipped.owner
        )
        assert response.status_code == 201
        outage = response.json()["outage"]
        assert outage["id"].startswith("og_")
        assert outage["status"] == "active"
        assert (outage["source"], outage["drill"], outage["speed"]) == ("manual", False, 1)
        assert outage["started_at"] == outage["last_check_in_at"] == "2026-10-20T18:00:00.000Z"
        assert outage["ended_at"] is None
        assert outage["acks"] == []
        assert outage["last_check_in_ok"] is True
        assert outage["plan"]["critical_runtime_minutes"] == 145.7
        keys = [m["key"] for m in outage["milestones"]]
        assert keys == [
            "power_lost",
            "conserve",
            "check_in",
            "halfway",
            "leave_by",
            "critical_low",
            "depleted",
        ]
        published = await collect(events)
    assert [e.name for e in published] == ["outage_started"]
    assert published[0].data["drill"] is False
    assert published[0].data["outage"]["id"] == outage["id"]


async def test_drill_runs_at_speed(client: httpx.AsyncClient, equipped: Home) -> None:
    outage = await equipped.start_outage(source="drill", speed=300)
    assert outage["drill"] is True
    assert outage["speed"] == 300
    start = datetime.fromisoformat(outage["started_at"])
    # 145.7 outage minutes at 300x is 29.1 real seconds.
    assert (at(outage, "depleted") - start).total_seconds() == pytest.approx(29.14, abs=0.01)
    assert (at(outage, "check_in") - start).total_seconds() == pytest.approx(12)


@pytest.mark.parametrize(
    "body",
    [
        {"source": "manual", "speed": 300},
        {"source": "drill", "speed": 601},
        {"source": "drill", "speed": 0.5},
        {"source": "earthquake"},
        {},
    ],
)
async def test_start_outage_validation(
    client: httpx.AsyncClient, equipped: Home, body: dict[str, Any]
) -> None:
    response = await client.post(f"{equipped.url}/outages", json=body, headers=equipped.owner)
    assert response.status_code == 400


async def test_speed_is_only_for_drills_message(client: httpx.AsyncClient, equipped: Home) -> None:
    response = await client.post(
        f"{equipped.url}/outages", json={"source": "manual", "speed": 5}, headers=equipped.owner
    )
    assert "speed is only allowed for drills" in response.json()["error"]["message"]


async def test_only_one_active_outage(client: httpx.AsyncClient, equipped: Home) -> None:
    await equipped.start_outage()
    for source in ("manual", "drill", "charging_lost"):
        response = await client.post(
            f"{equipped.url}/outages", json={"source": source}, headers=equipped.owner
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"


async def test_real_outage_replaces_a_running_drill(
    client: httpx.AsyncClient, equipped: Home, clock: FakeClock, context: AppContext
) -> None:
    drill = await equipped.start_outage(source="drill", speed=60)
    clock.advance(seconds=30)
    with context.broker.subscribe(equipped.id) as events:
        real = await equipped.start_outage(source="charging_lost")
        published = await collect(events)
    assert [(e.name, e.data["drill"]) for e in published] == [
        ("outage_ended", True),
        ("outage_started", False),
    ]
    history = (await client.get(f"{equipped.url}/outages", headers=equipped.owner)).json()
    assert [(o["id"], o["status"]) for o in history["outages"]] == [
        (real["id"], "active"),
        (drill["id"], "ended"),
    ]


async def test_alarm_must_be_armed_for_automatic_outages(
    client: httpx.AsyncClient, equipped: Home
) -> None:
    await client.patch(equipped.url, json={"armed": False}, headers=equipped.owner)
    response = await client.post(
        f"{equipped.url}/outages", json={"source": "charging_lost"}, headers=equipped.owner
    )
    assert response.status_code == 409
    assert (await equipped.start_outage(source="manual"))["status"] == "active"


async def test_current_outage(client: httpx.AsyncClient, equipped: Home) -> None:
    _, token = await equipped.join()
    response = await client.get(f"{equipped.url}/outages/current", headers=bearer(token))
    assert response.json() == {"outage": None}
    outage = await equipped.start_outage()
    response = await client.get(f"{equipped.url}/outages/current", headers=bearer(token))
    assert response.json()["outage"]["id"] == outage["id"]
    assert "plan" in response.json()["outage"]


async def test_leave_by_uses_the_nearest_hospital(
    client: httpx.AsyncClient, equipped: Home, external: respx.MockRouter
) -> None:
    external["overpass"].respond(
        200,
        json={
            "elements": [
                overpass_element(1, "library", "W. Dale Clark Library", 41.2585, -95.9345),
                overpass_element(2, "hospital", "Nebraska Medical Center", 41.2565, -95.9800),
            ]
        },
    )
    outage = await equipped.start_outage()
    leave_by = next(m for m in outage["milestones"] if m["key"] == "leave_by")
    # 3.8 km x 1.3 / 40 km/h -> 7.4 min + 3 min parking = 10 min
    assert "Nebraska Medical Center (10 min away)" in leave_by["detail"]
    start = datetime.fromisoformat(outage["started_at"])
    assert (at(outage, "leave_by") - start).total_seconds() / 60 == pytest.approx(
        145.7 - 10 - 30, abs=0.1
    )


async def test_check_in_recomputes_the_plan(
    client: httpx.AsyncClient, equipped: Home, clock: FakeClock, context: AppContext
) -> None:
    outage = await equipped.start_outage()
    depleted = at(outage, "depleted")
    clock.advance(minutes=45)
    with context.broker.subscribe(equipped.id) as events:
        response = await client.post(
            f"{equipped.url}/outages/current/check-in", json={"ok": True}, headers=equipped.owner
        )
        published = await collect(events)
    assert response.status_code == 200
    updated = response.json()["outage"]
    # No new readings: the countdown carries on where it was.
    assert updated["plan"]["critical_runtime_minutes"] == pytest.approx(145.7 - 45, abs=0.1)
    assert abs((at(updated, "depleted") - depleted).total_seconds()) < 10
    assert updated["last_check_in_at"] == "2026-10-20T18:45:00.000Z"
    assert (at(updated, "check_in") - clock.now).total_seconds() == 3600
    assert [e.name for e in published] == ["check_in"]


async def test_check_in_with_battery_readings(
    client: httpx.AsyncClient, equipped: Home, clock: FakeClock
) -> None:
    household = (await client.get(equipped.url, headers=equipped.owner)).json()["household"]
    vent = await equipped.add_device(kind="home_ventilator", label="Vent")
    station_id = household["backups"][0]["id"]
    await equipped.start_outage()
    clock.advance(minutes=10)
    response = await client.post(
        f"{equipped.url}/outages/current/check-in",
        json={
            "ok": False,
            "devices": [{"device_id": vent["id"], "charge_percent": 50}],
            "backups": [{"backup_id": station_id, "charge_percent": 50}],
        },
        headers=equipped.owner,
    )
    outage = response.json()["outage"]
    assert outage["last_check_in_ok"] is False
    # Half the station left (500 Wh x 0.85 / 350 W x 60 = 72.9 min) for the concentrator.
    assert outage["plan"]["critical_runtime_minutes"] == 72.9


async def test_check_in_errors(client: httpx.AsyncClient, equipped: Home) -> None:
    url = f"{equipped.url}/outages/current/check-in"
    response = await client.post(url, json={}, headers=equipped.owner)
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "There is no active outage."

    await equipped.start_outage()
    response = await client.post(url, headers=equipped.owner)  # no body: just "I'm OK"
    assert response.status_code == 200
    response = await client.post(
        url,
        json={"devices": [{"device_id": "dv_unknown", "charge_percent": 50}]},
        headers=equipped.owner,
    )
    assert response.status_code == 400
    assert "dv_unknown" in response.json()["error"]["message"]

    _, token = await equipped.join()
    response = await client.post(url, json={}, headers=bearer(token))
    assert response.status_code == 403


async def test_caregiver_ack(
    client: httpx.AsyncClient, equipped: Home, clock: FakeClock, context: AppContext
) -> None:
    caregiver, token = await equipped.join("Maya")
    url = f"{equipped.url}/outages/current/ack"
    assert (await client.post(url, json={}, headers=bearer(token))).status_code == 404

    outage = await equipped.start_outage()
    clock.advance(minutes=2)
    with context.broker.subscribe(equipped.id) as events:
        response = await client.post(
            url,
            json={"message": "On my way with the generator", "eta_minutes": 25},
            headers=bearer(token),
        )
        published = await collect(events)
    assert response.status_code == 200
    ack = response.json()["outage"]["acks"][0]
    assert ack == {
        "caregiver_id": caregiver["id"],
        "name": "Maya",
        "message": "On my way with the generator",
        "eta_minutes": 25,
        "at": "2026-10-20T18:02:00.000Z",
    }
    assert [e.name for e in published] == ["ack"]
    assert published[0].data == {"outage_id": outage["id"], "ack": ack}

    response = await client.post(url, json={}, headers=equipped.owner)
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Only caregivers can do this."
    response = await client.post(url, json={"eta_minutes": -5}, headers=bearer(token))
    assert response.status_code == 400


async def test_end_outage(
    client: httpx.AsyncClient, equipped: Home, clock: FakeClock, context: AppContext
) -> None:
    await equipped.start_outage(source="drill", speed=10)
    clock.advance(minutes=5)
    with context.broker.subscribe(equipped.id) as events:
        response = await client.post(f"{equipped.url}/outages/current/end", headers=equipped.owner)
        published = await collect(events)
    outage = response.json()["outage"]
    assert outage["status"] == "ended"
    assert outage["ended_at"] == "2026-10-20T18:05:00.000Z"
    assert [(e.name, e.data["drill"]) for e in published] == [("outage_ended", True)]
    response = await client.post(f"{equipped.url}/outages/current/end", headers=equipped.owner)
    assert response.status_code == 404
    current = await client.get(f"{equipped.url}/outages/current", headers=equipped.owner)
    assert current.json() == {"outage": None}


async def test_history_is_newest_first_without_plans(
    client: httpx.AsyncClient, equipped: Home, clock: FakeClock
) -> None:
    ids = []
    for _ in range(3):
        ids.append((await equipped.start_outage())["id"])
        clock.advance(minutes=1)
        await client.post(f"{equipped.url}/outages/current/end", headers=equipped.owner)
    response = await client.get(f"{equipped.url}/outages", headers=equipped.owner)
    outages = response.json()["outages"]
    assert [o["id"] for o in outages] == ids[::-1]
    assert all("plan" not in o and "milestones" in o for o in outages)

    _, token = await equipped.join()
    response = await client.get(f"{equipped.url}/outages", headers=bearer(token))
    assert response.status_code == 403


async def test_check_in_and_ticker_do_not_clash(
    client: httpx.AsyncClient, equipped: Home, clock: FakeClock, ticker: OutageTicker
) -> None:
    await equipped.start_outage()
    clock.advance(minutes=90)
    results = await asyncio.gather(
        ticker.tick(),
        client.post(f"{equipped.url}/outages/current/check-in", headers=equipped.owner),
        ticker.tick(),
    )
    assert results[1].status_code == 200
