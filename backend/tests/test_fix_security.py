"""Regression tests for the security review: limits under concurrency, join-code guessing,
revoking caregivers, stale announcements, and request-size limits.
"""

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import func, select

from app import models
from app.api import stream as stream_module
from app.api.deps import _household_access
from app.auth import JoinCodeGuard, Principal, client_key
from app.context import AppContext
from app.errors import ApiError
from app.events import EventBroker
from app.geocoding import GeocodeResult
from app.schemas import BackupCreate, HouseholdResponse
from app.services.households import (
    MAX_BACKUPS,
    MAX_CAREGIVERS,
    MAX_DEVICES,
    HouseholdService,
    load_household,
)
from tests.support import FakeClock, Home, bearer, collect, create_home, overpass_element

JOIN_URL = "/api/caregivers/join"


def client_at(app: FastAPI, ip: str) -> httpx.AsyncClient:
    """An in-process client whose requests come from this IP address."""
    transport = httpx.ASGITransport(app=app, client=(ip, 50000))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def join_as(app: FastAPI, ip: str, code: str, name: str = "Maya") -> httpx.Response:
    async with client_at(app, ip) as http:
        return await http.post(JOIN_URL, json={"join_code": code, "name": name})


def wrong_code(home: Home) -> str:
    """A well-formed join code that isn't this household's."""
    return "ZZZZZZ" if home.join_code != "ZZZZZZ" else "YYYYYY"


# --- Finding 12: limits hold under concurrent requests ------------------------------------


async def test_concurrent_backup_adds_cannot_pass_the_limit(
    client: httpx.AsyncClient, home: Home
) -> None:
    responses = await asyncio.gather(
        *(
            client.post(f"{home.url}/backups", json={"type": "power_station"}, headers=home.owner)
            for _ in range(40)
        )
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses.count(201) == MAX_BACKUPS
    assert set(statuses) == {201, 409}
    household = (await client.get(home.url, headers=home.owner)).json()["household"]
    assert len(household["backups"]) == MAX_BACKUPS


async def test_concurrent_device_adds_cannot_pass_the_limit(
    client: httpx.AsyncClient, home: Home
) -> None:
    responses = await asyncio.gather(
        *(
            client.post(f"{home.url}/devices", json={"kind": "custom"}, headers=home.owner)
            for _ in range(MAX_DEVICES + 15)
        )
    )
    assert [r.status_code for r in responses].count(201) == MAX_DEVICES
    household = (await client.get(home.url, headers=home.owner)).json()["household"]
    assert len(household["devices"]) == MAX_DEVICES


async def test_concurrent_joins_cannot_pass_the_caregiver_limit(app: FastAPI, home: Home) -> None:
    # Each would-be caregiver joins from their own address, as real families do.
    responses = await asyncio.gather(
        *(
            join_as(app, f"10.0.{i}.1", home.join_code, f"Helper {i}")
            for i in range(MAX_CAREGIVERS + 15)
        )
    )
    assert [r.status_code for r in responses].count(201) == MAX_CAREGIVERS
    async with client_at(app, "10.9.9.9") as http:
        household = (await http.get(home.url, headers=home.owner)).json()["household"]
    assert len(household["caregivers"]) == MAX_CAREGIVERS


# --- Finding 13: join codes can't be guessed at full speed ---------------------------------


async def test_wrong_join_codes_are_limited_per_client(
    app: FastAPI, context: AppContext, home: Home, clock: FakeClock
) -> None:
    limit = context.settings.join_attempts_per_client
    for _ in range(limit):
        assert (await join_as(app, "203.0.113.5", wrong_code(home))).status_code == 404
    blocked = await join_as(app, "203.0.113.5", wrong_code(home))
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "unavailable"
    # While blocked, even the right code is refused, so guessing can't continue.
    assert (await join_as(app, "203.0.113.5", home.join_code)).status_code == 429
    # Other people are not affected.
    assert (await join_as(app, "198.51.100.7", home.join_code)).status_code == 201
    # After the window the client may try again.
    clock.advance(seconds=context.settings.join_attempt_window_seconds + 1)
    assert (await join_as(app, "203.0.113.5", home.join_code)).status_code == 201


async def test_parallel_guesses_cannot_slip_past_the_limit(
    app: FastAPI, context: AppContext, home: Home
) -> None:
    responses = await asyncio.gather(
        *(join_as(app, "203.0.113.9", wrong_code(home)) for _ in range(60))
    )
    statuses = [r.status_code for r in responses]
    assert statuses.count(404) == context.settings.join_attempts_per_client
    assert statuses.count(429) == 60 - context.settings.join_attempts_per_client


async def test_guessing_from_many_addresses_hits_an_overall_limit(
    app: FastAPI, context: AppContext, home: Home
) -> None:
    settings = context.settings
    per_client = settings.join_attempts_per_client
    clients = -(-settings.join_attempts_overall // per_client)  # ceiling division
    for i in range(clients):
        for _ in range(per_client):
            await join_as(app, f"192.0.2.{i + 1}", wrong_code(home))
    # A client that has already guessed wrong is refused once everyone together hit the limit...
    await join_as(app, "198.51.100.200", wrong_code(home))
    response = await join_as(app, "198.51.100.200", home.join_code)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "unavailable"
    # ...but someone with no wrong codes still gets a try, so guessing can't pause every join.
    assert (await join_as(app, "198.51.100.77", home.join_code)).status_code == 201


async def test_ipv6_guesses_count_per_network(
    app: FastAPI, context: AppContext, home: Home
) -> None:
    # One home or attacker usually controls a whole /56, so rotating addresses or /64s inside
    # it doesn't buy more guesses.
    for i in range(context.settings.join_attempts_per_client):
        response = await join_as(app, f"2001:db8:1:{i:x}::{i + 1:x}", wrong_code(home))
        assert response.status_code == 404
    assert (await join_as(app, "2001:db8:1:ff::1", home.join_code)).status_code == 429
    assert (await join_as(app, "2001:db8:1:100::1", home.join_code)).status_code == 201


async def test_successful_joins_do_not_use_up_the_limit(
    app: FastAPI, context: AppContext, home: Home
) -> None:
    # A family joining from one home Wi-Fi network is never slowed down.
    for i in range(context.settings.join_attempts_per_client + 5):
        response = await join_as(app, "203.0.113.20", home.join_code, f"Relative {i}")
        assert response.status_code == 201


@pytest.mark.parametrize(
    ("host", "key"),
    [
        ("203.0.113.5", "203.0.113.5"),
        ("2001:db8:1:2::7", "2001:db8:1::/56"),
        ("2001:db8:1:2aa:bbbb:bbbb:cccc:dddd", "2001:db8:1:200::/56"),
        ("::ffff:203.0.113.5", "203.0.113.5"),
        ("testclient", "testclient"),
        (None, "unknown"),
    ],
)
def test_client_key(host: str | None, key: str) -> None:
    assert client_key(host) == key


def test_join_code_guard_counts_wrong_codes_in_a_sliding_window() -> None:
    clock = FakeClock()
    guard = JoinCodeGuard(clock, per_client=3, overall=5, window=timedelta(minutes=10))
    for _ in range(3):
        guard.begin("a")  # wrong codes: never marked as succeeded
        clock.advance(minutes=1)
    with pytest.raises(ApiError) as error:
        guard.begin("a")
    assert (error.value.status_code, error.value.code) == (429, "unavailable")
    assert "10 minutes" in error.value.message

    guard.succeeded(guard.begin("b"))  # right codes don't count
    guard.begin("b")
    guard.begin("c")
    with pytest.raises(ApiError):  # 5 wrong codes in all, and "c" has guessed wrong already
        guard.begin("c")
    guard.begin("d")  # a client with no wrong codes still gets one try
    with pytest.raises(ApiError):
        guard.begin("d")

    clock.advance(minutes=7, seconds=1)  # the first wrong code from "a" is now 10 minutes old
    with pytest.raises(ApiError):  # still 5 in all
        guard.begin("a")
    clock.advance(minutes=1)  # and its second
    guard.begin("a")
    with pytest.raises(ApiError):
        guard.begin("a")


def test_join_code_guard_forgets_everything_old() -> None:
    clock = FakeClock()
    guard = JoinCodeGuard(clock, per_client=2, overall=100, window=timedelta(minutes=10))
    for i in range(50):
        guard.begin(f"client {i}")
    clock.advance(minutes=11)
    guard.begin("client 0")
    assert guard._wrong_total == 1
    assert dict(guard._wrong_by_client) == {"client 0": 1}


# --- Finding 14: removing a caregiver really revokes access -------------------------------


async def test_removed_caregiver_cannot_rejoin_with_the_code_they_saw(
    client: httpx.AsyncClient, home: Home, context: AppContext
) -> None:
    caregiver, token = await home.join("Ex")
    seen_code = (await client.get(home.url, headers=bearer(token))).json()["household"]["join_code"]
    with context.broker.subscribe(home.id, caregiver["id"]) as removed_stream:
        response = await client.delete(
            f"{home.url}/caregivers/{caregiver['id']}", headers=home.owner
        )
        assert response.status_code == 204
        # Their stream ends without being sent the household with its new code.
        assert await asyncio.wait_for(removed_stream.next(), 1) is None

    rejoin = await client.post(JOIN_URL, json={"join_code": seen_code, "name": "Ex"})
    assert rejoin.status_code == 404
    new_code = (await client.get(home.url, headers=home.owner)).json()["household"]["join_code"]
    assert new_code != seen_code
    # The family can still invite people with the new code.
    response = await client.post(JOIN_URL, json={"join_code": new_code, "name": "Maya"})
    assert response.status_code == 201


async def test_member_access_rechecks_a_removed_caregiver(
    client: httpx.AsyncClient, home: Home, context: AppContext
) -> None:
    # A request whose token was checked just before the caregiver was removed must not then
    # read the household (with its new join code).
    caregiver, _ = await home.join("Ex")
    await client.delete(f"{home.url}/caregivers/{caregiver['id']}", headers=home.owner)
    stale = Principal("caregiver", home.id, caregiver["id"], "Ex")
    check = _household_access(None)
    async with context.db.sessions() as session:
        with pytest.raises(ApiError) as error:
            await check(home.id, stale, session)
    assert error.value.status_code == 401


# --- Finding 18: a whole-object PATCH with a new address re-locates the home --------------


async def test_whole_object_patch_with_new_address_replaces_coordinates(
    client: httpx.AsyncClient, home: Home, external: respx.MockRouter
) -> None:
    external["census"].respond(200, json={"result": {"addressMatches": []}})
    external["nominatim"].respond(
        200,
        json=[{"lat": "40.81", "lon": "-96.70", "address": {"ISO3166-2-lvl4": "US-NE"}}],
    )
    household = (await client.get(home.url, headers=home.owner)).json()["household"]
    household["address"] = "9 Oak St, Lincoln, NE"  # lat/lon still the old (Omaha) ones
    response = await client.patch(home.url, json=household, headers=home.owner)
    assert response.status_code == 200
    updated = response.json()["household"]
    assert (updated["lat"], updated["lon"]) == (40.81, -96.70)
    assert updated["address"] == "9 Oak St, Lincoln, NE"


async def test_new_address_with_new_coordinates_keeps_the_coordinates_sent(
    client: httpx.AsyncClient, home: Home, external: respx.MockRouter
) -> None:
    response = await client.patch(
        home.url,
        json={
            "address": "9 Oak St, Council Bluffs, IA",
            "lat": 41.26,
            "lon": -95.86,
            "state": "IA",
        },
        headers=home.owner,
    )
    updated = response.json()["household"]
    assert (updated["lat"], updated["lon"], updated["state"]) == (41.26, -95.86, "IA")
    assert not external["census"].called and not external["nominatim"].called


async def test_whole_object_patch_without_changes_looks_nothing_up(
    client: httpx.AsyncClient, home: Home, external: respx.MockRouter
) -> None:
    household = (await client.get(home.url, headers=home.owner)).json()["household"]
    household["name"] = "Grandpa's house"
    response = await client.patch(home.url, json=household, headers=home.owner)
    assert response.json()["household"]["name"] == "Grandpa's house"
    assert (response.json()["household"]["lat"], response.json()["household"]["lon"]) == (
        41.2565,
        -95.9345,
    )
    assert not external["census"].called and not external["nominatim"].called


async def test_whole_object_patch_adds_a_location_to_a_home_without_one(
    client: httpx.AsyncClient, external: respx.MockRouter
) -> None:
    external["census"].respond(
        200,
        json={
            "result": {
                "addressMatches": [
                    {"coordinates": {"x": -95.93, "y": 41.26}, "addressComponents": {"state": "NE"}}
                ]
            }
        },
    )
    home = await create_home(client)  # no address, no coordinates
    household = (await client.get(home.url, headers=home.owner)).json()["household"]
    household["address"] = "1200 Douglas St, Omaha, NE"  # lat/lon sent back as null
    updated = (await client.patch(home.url, json=household, headers=home.owner)).json()
    assert (updated["household"]["lat"], updated["household"]["lon"]) == (41.26, -95.93)
    assert updated["household"]["state"] == "NE"


# --- Finding 35: announcements and responses show what is in the database ----------------


async def test_slow_address_patch_does_not_announce_stale_state(
    client: httpx.AsyncClient, home: Home, context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def slow_geocode(address: str) -> GeocodeResult:
        await asyncio.sleep(0.3)  # the Census geocoder taking its time
        return GeocodeResult(lat=41.26, lon=-95.93, state="NE", source="census")

    monkeypatch.setattr(context.geocoder, "geocode", slow_geocode)

    async def other_tab() -> None:
        await asyncio.sleep(0.05)
        response = await client.patch(home.url, json={"armed": False}, headers=home.owner)
        assert response.status_code == 200
        await home.join("Maya")

    with context.broker.subscribe(home.id) as events:
        patched, _ = await asyncio.gather(
            client.patch(home.url, json={"address": "1 Main St, Omaha NE"}, headers=home.owner),
            other_tab(),
        )
        published = await collect(events)

    stored = (await client.get(home.url, headers=home.owner)).json()["household"]
    assert stored["armed"] is False and len(stored["caregivers"]) == 1
    assert published[-1].data["household"] == stored
    assert patched.json()["household"] == stored


async def test_changes_announce_what_others_changed_meanwhile(
    client: httpx.AsyncClient, home: Home, context: AppContext
) -> None:
    # The household this request loaded is older than a device edit and a caregiver who joined.
    device = await home.add_device(kind="home_ventilator", label="Ventilator")
    async with context.db.sessions() as session:
        household = await load_household(session, home.id)
        response = await client.patch(
            f"{home.url}/devices/{device['id']}", json={"label": "Dad's vent"}, headers=home.owner
        )
        assert response.status_code == 200
        await home.join("Maya")
        service = HouseholdService(session, context.broker, context.geocoder, context.clock)
        with context.broker.subscribe(home.id) as events:
            await service.add_backup(household, BackupCreate(type="power_station"))
            [event] = await collect(events)
    announced = event.data["household"]
    assert [c["name"] for c in announced["caregivers"]] == ["Maya"]
    assert [d["label"] for d in announced["devices"]] == ["Dad's vent"]
    assert len(announced["backups"]) == 1
    stored = (await client.get(home.url, headers=home.owner)).json()["household"]
    assert announced == stored


async def test_a_write_waits_for_another_requests_write(context: AppContext, home: Home) -> None:
    # The mechanism the household service relies on: once a session has written, another
    # session's write waits until the first commits, and then sees what it wrote.
    order: list[str] = []

    async def add_caregiver(name: str, start_after: float, hold: float) -> int:
        async with context.db.sessions() as session:
            await load_household(session, home.id)  # an earlier read must not pin old data
            await asyncio.sleep(start_after)
            session.add(
                models.Caregiver(
                    id=f"cg_{name}",
                    household_id=home.id,
                    name=name,
                    relationship=None,
                    token_hash=name * 64,
                    joined_at=context.clock(),
                )
            )
            await session.flush()
            order.append(f"{name} wrote")
            count = await session.scalar(
                select(func.count())
                .select_from(models.Caregiver)
                .where(models.Caregiver.household_id == home.id)
            )
            await asyncio.sleep(hold)
            await session.commit()
            order.append(f"{name} done")
            return count or 0

    first, second = await asyncio.gather(add_caregiver("A", 0, 0.3), add_caregiver("B", 0.05, 0))
    assert order == ["A wrote", "A done", "B wrote", "B done"]
    assert (first, second) == (1, 2)


# --- Findings 34 and 36: streams of removed caregivers really end --------------------------


async def first_message(events: AsyncGenerator[Any]) -> Any:
    """The first message a stream sends (None if it ends at once); the stream then stops."""
    try:
        async with asyncio.timeout(2):
            return await anext(events, None)
    finally:
        await events.aclose()


async def test_stream_that_connects_as_its_caregiver_is_removed_gets_nothing(
    client: httpx.AsyncClient, home: Home, context: AppContext
) -> None:
    caregiver, _ = await home.join("Ex")
    # The handler has checked the token; the generator hasn't subscribed yet when the owner
    # removes the caregiver (and so the removal's close() can't reach this stream).
    events = stream_module._events(context, home.id, caregiver["id"])
    await client.delete(f"{home.url}/caregivers/{caregiver['id']}", headers=home.owner)
    assert await first_message(events) is None


async def test_stream_of_another_households_caregiver_gets_nothing(
    client: httpx.AsyncClient, home: Home, context: AppContext
) -> None:
    other = await create_home(client)
    caregiver, _ = await other.join("Maya")
    events = stream_module._events(context, home.id, caregiver["id"])
    assert await first_message(events) is None


async def test_owner_and_caregiver_streams_still_start_with_a_snapshot(
    home: Home, context: AppContext
) -> None:
    caregiver, _ = await home.join("Maya")
    for viewer in (None, caregiver["id"]):
        message = await first_message(stream_module._events(context, home.id, viewer))
        assert message.event == "snapshot"


# Payloads are never read in these tests, so an empty one will do.
EMPTY_PAYLOAD = HouseholdResponse.model_construct(household=None)  # type: ignore[arg-type]


async def test_close_is_not_lost_when_the_queue_overflows() -> None:
    broker = EventBroker(queue_size=4)
    with broker.subscribe("hh", "cg_removed") as subscription:
        broker.close("hh", "cg_removed")
        for _ in range(10):
            broker.publish("hh", "household_updated", EMPTY_PAYLOAD)
        assert await asyncio.wait_for(subscription.next(), 1) is None


async def test_closing_wakes_a_waiting_stream_and_drops_what_was_queued() -> None:
    broker = EventBroker(queue_size=4)
    with broker.subscribe("hh", "cg_removed") as removed, broker.subscribe("hh") as owner:
        broker.publish("hh", "household_updated", EMPTY_PAYLOAD)
        assert (await owner.next()) is not None
        waiting = asyncio.create_task(owner.next())
        broker.publish("hh", "household_updated", EMPTY_PAYLOAD)  # queued for `removed`
        broker.close("hh", "cg_removed")
        assert await asyncio.wait_for(removed.next(), 1) is None
        assert (await asyncio.wait_for(waiting, 1)) is not None  # the owner's stream goes on
        broker.close("hh")
        assert await asyncio.wait_for(owner.next(), 1) is None


async def test_a_stream_that_falls_too_far_behind_is_ended_not_silently_thinned() -> None:
    broker = EventBroker(queue_size=4)
    with broker.subscribe("hh") as subscription:
        for _ in range(5):
            broker.publish("hh", "household_updated", EMPTY_PAYLOAD)
        # The client must reconnect for a fresh snapshot rather than miss an alert.
        assert await asyncio.wait_for(subscription.next(), 1) is None
        assert subscription.closed


# --- Finding 19: help-sites accepts the empty lat/lon the contract's URL form produces ----


async def test_help_sites_with_empty_lat_lon_uses_the_household_location(
    client: httpx.AsyncClient, home: Home, external: respx.MockRouter
) -> None:
    external["overpass"].respond(
        200, json={"elements": [overpass_element(1, "fire_station", "Station 1", 41.27, -95.93)]}
    )
    response = await client.get(f"{home.url}/help-sites?lat=&lon=&radius_km=15", headers=home.owner)
    assert response.status_code == 200, response.text
    assert response.json()["sites"][0]["name"] == "Station 1"


async def test_help_sites_with_everything_empty_uses_defaults(
    client: httpx.AsyncClient, home: Home, external: respx.MockRouter
) -> None:
    external["overpass"].respond(200, json={"elements": []})
    response = await client.get(f"{home.url}/help-sites?lat=&lon=&radius_km=", headers=home.owner)
    assert response.status_code == 200, response.text
    body = external["overpass"].calls.last.request.content.decode()
    assert "around%3A15000" in body or "around:15000" in body


@pytest.mark.parametrize("query", ["lat=41&lon=", "lat=abc&lon=-95", "lat=41&lon=-95&radius_km=0"])
async def test_help_sites_still_rejects_bad_positions(
    client: httpx.AsyncClient, home: Home, query: str
) -> None:
    response = await client.get(f"{home.url}/help-sites?{query}", headers=home.owner)
    assert response.status_code == 400


# --- Findings 11, 23 and 15: identify-device can't be made to do unbounded work -----------

IDENTIFY_URL = "/api/ai/identify-device"


PHOTO = ("label.jpg", b"\xff\xd8\xff", "image/jpeg")


async def test_multipart_text_has_the_same_limit_as_json(client: httpx.AsyncClient) -> None:
    started = time.perf_counter()
    # `(None, value)` makes httpx send a plain multipart field rather than a file. Before the
    # fix, this 90 KB name froze the server for over ten seconds of fuzzy matching.
    response = await client.post(IDENTIFY_URL, files={"text": (None, "ab " * 30_000)})
    assert response.status_code == 400
    assert time.perf_counter() - started < 2  # rejected before any fuzzy matching

    too_long: list[dict[str, Any]] = [
        {"text": (None, "x" * 501)},
        {"text": (None, "x" * 501), "image": PHOTO},
    ]
    for files in too_long:
        response = await client.post(IDENTIFY_URL, files=files)
        assert response.status_code == 400
        assert "500 characters" in response.json()["error"]["message"]

    # Text within the JSON limit still works as multipart, alone or beside a photo.
    fine: list[dict[str, Any]] = [
        {"text": (None, "Astral 150")},
        {"text": (None, "Astral 150"), "image": PHOTO},
    ]
    for files in fine:
        response = await client.post(IDENTIFY_URL, files=files)
        assert response.status_code == 200
        assert response.json()["match"]["kind"] == "home_ventilator"


async def test_identify_device_requests_that_still_work(client: httpx.AsyncClient) -> None:
    response = await client.post(IDENTIFY_URL, json={"text": "x" * 500})
    assert response.status_code == 200
    response = await client.post(IDENTIFY_URL, json={"text": "Astral 150 " + " " * 3000})
    assert response.status_code == 200  # surrounding spaces are trimmed, as before
    # A photo alone (with an empty text field) still reaches the photo path.
    response = await client.post(IDENTIFY_URL, files={"text": (None, ""), "image": PHOTO})
    assert response.status_code == 503  # no API key in tests: photos need Claude
    big_jpeg = b"\xff\xd8\xff" + b"\xff" * 3_400_000  # starts like a JPEG: photos are sniffed
    response = await client.post(IDENTIFY_URL, files={"image": ("big.jpg", big_jpeg)})
    assert response.status_code == 503  # a 3.4 MB photo is within the limit


class CountingBody:
    """A request body sent in chunks with no Content-Length, counting what the server reads."""

    def __init__(self, head: bytes, chunk: bytes, chunks: int) -> None:
        self.head, self.chunk, self.chunks = head, chunk, chunks
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.sent += len(self.head)
        yield self.head
        for _ in range(self.chunks):
            self.sent += len(self.chunk)
            yield self.chunk


async def test_chunked_photo_upload_is_cut_off_at_the_limit(client: httpx.AsyncClient) -> None:
    boundary = "lifeline"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="a.jpg"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode()
    body = CountingBody(head, b"\xff" * (1024 * 1024), chunks=40)
    response = await client.post(
        IDENTIFY_URL,
        content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert response.status_code == 400
    assert "under 3.5 MB" in response.json()["error"]["message"]
    assert body.sent <= 7 * 1024 * 1024


async def test_json_body_is_cut_off_at_its_limit(client: httpx.AsyncClient) -> None:
    body = CountingBody(b'{"text": "', b"a" * (1024 * 1024), chunks=60)
    response = await client.post(
        IDENTIFY_URL, content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert body.sent <= 2 * 1024 * 1024
