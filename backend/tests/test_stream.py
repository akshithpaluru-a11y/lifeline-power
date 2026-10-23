"""The live event stream, tested against a real uvicorn server.

(httpx's in-process ASGI transport buffers the whole response, so it can't read a stream that
never ends.)
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import respx
import uvicorn
from fastapi import FastAPI

from app.config import Settings
from app.context import AppContext
from app.main import create_app
from app.ticker import OutageTicker
from tests.support import FakeClock, Home, bearer, create_home

TIMEOUT_SECONDS = 5


@dataclass
class Message:
    """One SSE message as raw lines, plus its parsed event name and data."""

    lines: list[str]

    @property
    def event(self) -> str:
        return next(line[len("event: ") :] for line in self.lines if line.startswith("event: "))

    @property
    def data(self) -> Any:
        return json.loads(self.raw_data)

    @property
    def raw_data(self) -> str:
        return next(line[len("data: ") :] for line in self.lines if line.startswith("data: "))


class EventStream:
    """Reads an open text/event-stream response message by message."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self._lines = response.aiter_lines()

    async def message(self, *, include_comments: bool = False) -> Message:
        """The next message (by default skipping keep-alive comments)."""
        async with asyncio.timeout(TIMEOUT_SECONDS):
            while True:
                lines: list[str] = []
                async for line in self._lines:
                    if line == "":
                        break
                    lines.append(line)
                else:
                    raise EOFError("stream closed")
                if include_comments or not all(line.startswith(":") for line in lines):
                    return Message(lines)

    async def until(self, event: str) -> Message:
        """Skip ahead to the next message with this event name."""
        while (message := await self.message()).event != event:
            pass
        return message

    async def closed(self) -> bool:
        """Whether the server ends the stream (within the timeout)."""
        try:
            while True:
                await self.message()
        except EOFError:
            return True


@dataclass
class LiveServer:
    app: FastAPI
    client: httpx.AsyncClient

    @property
    def ticker(self) -> OutageTicker:
        ticker: OutageTicker = self.app.state.ticker
        return ticker

    @asynccontextmanager
    async def stream(self, household_id: str, token: str) -> AsyncIterator[EventStream]:
        url = f"/api/households/{household_id}/stream"
        async with self.client.stream("GET", url, params={"token": token}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            yield EventStream(response)


@pytest.fixture
async def live(
    settings: Settings, clock: FakeClock, external: respx.MockRouter
) -> AsyncIterator[LiveServer]:
    external.route(host="127.0.0.1").pass_through()
    app = create_app(settings.model_copy(update={"sse_ping_seconds": 0.2}), clock=clock)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(TIMEOUT_SECONDS):
        while not server.started:  # noqa: ASYNC110  (uvicorn only offers a flag)
            await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=TIMEOUT_SECONDS
        ) as client:
            yield LiveServer(app, client)
    finally:
        server.should_exit = True
        await task


@pytest.fixture
async def live_home(live: LiveServer) -> Home:
    home = await create_home(live.client, lat=41.2565, lon=-95.9345, state="NE")
    await home.add_device(
        kind="oxygen_concentrator_home", label="Oxygen", notes="Line one\nLine two"
    )
    await home.add_backup(type="power_station")
    return home


async def test_snapshot_on_connect(live: LiveServer, live_home: Home) -> None:
    _, caregiver_token = await live_home.join()
    async with live.stream(live_home.id, caregiver_token) as events:
        snapshot = await events.message()
    assert snapshot.event == "snapshot"
    assert snapshot.data["household"]["id"] == live_home.id
    assert snapshot.data["outage"] is None


async def test_every_message_is_one_line_of_compact_json(live: LiveServer, live_home: Home) -> None:
    async with live.stream(live_home.id, live_home.owner_token) as events:
        await live_home.start_outage(source="drill", speed=300)
        messages = [await events.message() for _ in range(2)]
    for message in messages:
        assert [line.split(":")[0] for line in message.lines] == ["event", "data"]
        data = message.data
        assert message.raw_data == json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    # The device note's newline is escaped inside the JSON string, not split across lines.
    assert messages[0].data["household"]["devices"][0]["notes"] == "Line one\nLine two"
    assert messages[1].event == "outage_started"
    assert messages[1].data["outage"]["drill"] is True


async def test_keep_alive_ping_comment(live: LiveServer, live_home: Home) -> None:
    async with live.stream(live_home.id, live_home.owner_token) as events:
        await events.message()  # snapshot
        ping = await events.message(include_comments=True)
    assert ping.lines == [": ping"]


async def test_outage_events_reach_caregivers(
    live: LiveServer, live_home: Home, clock: FakeClock
) -> None:
    _, caregiver_token = await live_home.join("Maya")
    url = live_home.url
    async with live.stream(live_home.id, caregiver_token) as events:
        assert (await events.message()).event == "snapshot"

        outage = await live_home.start_outage(source="drill", speed=300)
        started = await events.until("outage_started")
        assert started.data == {"outage": started.data["outage"], "drill": True}
        assert started.data["outage"]["id"] == outage["id"]

        clock.advance(seconds=13)  # 65 outage minutes: the hourly check-in is due
        await live.ticker.tick()
        milestone = await events.until("milestone")
        assert milestone.data["milestone"]["key"] == "check_in"
        assert milestone.data["drill"] is True

        clock.advance(seconds=4)  # 85 outage minutes: 20 past the check-in, 15 overdue
        await live.ticker.tick()
        missed = await events.until("missed_check_in")
        assert missed.data["outage_id"] == outage["id"]
        assert missed.data["minutes_overdue"] >= 15

        await live.client.post(
            f"{url}/outages/current/ack",
            json={"message": "On my way", "eta_minutes": 20},
            headers=bearer(caregiver_token),
        )
        ack = await events.until("ack")
        assert ack.data["ack"]["message"] == "On my way"

        await live.client.post(f"{url}/outages/current/check-in", headers=live_home.owner)
        check_in = await events.until("check_in")
        assert (
            check_in.data["outage"]["last_check_in_at"]
            == check_in.data["outage"]["plan"]["computed_at"]
        )

        await live.client.post(f"{url}/outages/current/end", headers=live_home.owner)
        ended = await events.until("outage_ended")
        assert ended.data["outage"]["status"] == "ended"
        assert ended.data["drill"] is True

        await live.client.patch(url, json={"armed": False}, headers=live_home.owner)
        updated = await events.until("household_updated")
        assert updated.data["household"]["armed"] is False


async def test_snapshot_includes_the_active_outage(live: LiveServer, live_home: Home) -> None:
    outage = await live_home.start_outage()
    async with live.stream(live_home.id, live_home.owner_token) as events:
        snapshot = await events.message()
    assert snapshot.data["outage"]["id"] == outage["id"]
    assert snapshot.data["outage"]["plan"]["critical_runtime_minutes"] == 145.7


async def test_stream_needs_a_valid_token_for_this_household(
    live: LiveServer, live_home: Home
) -> None:
    other = await create_home(live.client)
    url = f"{live_home.url}/stream"
    response = await live.client.get(url)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    response = await live.client.get(url, params={"token": "llo_nope"})
    assert response.status_code == 401
    response = await live.client.get(url, params={"token": other.owner_token})
    assert response.status_code == 403
    # The Authorization header works too.
    async with live.client.stream("GET", url, headers=live_home.owner) as response:
        assert response.status_code == 200


async def test_removed_caregiver_is_disconnected(live: LiveServer, live_home: Home) -> None:
    caregiver, caregiver_token = await live_home.join()
    async with (
        live.stream(live_home.id, caregiver_token) as caregiver_events,
        live.stream(live_home.id, live_home.owner_token) as owner_events,
    ):
        await caregiver_events.message()
        await owner_events.message()
        await live.client.delete(
            f"{live_home.url}/caregivers/{caregiver['id']}", headers=live_home.owner
        )
        assert await caregiver_events.closed()
        assert (await owner_events.message()).event == "household_updated"


async def test_deleting_the_household_closes_its_streams(live: LiveServer, live_home: Home) -> None:
    async with live.stream(live_home.id, live_home.owner_token) as events:
        await events.message()
        await live.client.delete(live_home.url, headers=live_home.owner)
        assert await events.closed()


async def test_clients_that_leave_at_once_are_cleaned_up(live: LiveServer, live_home: Home) -> None:
    # A phone that reconnects quickly may drop the stream while the snapshot is being read.
    for _ in range(5):
        async with live.client.stream(
            "GET", f"{live_home.url}/stream", headers=live_home.owner
        ) as response:
            assert response.status_code == 200
    context: AppContext = live.app.state.context
    async with asyncio.timeout(TIMEOUT_SECONDS):
        while context.broker.subscriber_count(live_home.id):  # noqa: ASYNC110
            await asyncio.sleep(0.01)
