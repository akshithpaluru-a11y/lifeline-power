"""Regression tests for revoking a caregiver's live stream, against a real uvicorn server."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from sse_starlette import ServerSentEvent

from app.api import stream as stream_module
from app.context import AppContext
from tests import test_stream
from tests.support import Home
from tests.test_stream import EventStream, LiveServer

# The live-server fixtures from the stream tests.
live = test_stream.live
live_home = test_stream.live_home


async def messages_until_closed(events: EventStream) -> list[str]:
    """The event names a stream sends until the server ends it."""
    names: list[str] = []
    try:
        while True:
            names.append((await events.message()).event)
    except EOFError:
        return names


async def test_stream_connecting_while_its_caregiver_is_removed_is_ended(
    live: LiveServer, live_home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    caregiver, token = await live_home.join("Ex")
    removed = asyncio.Event()
    original = stream_module._events

    async def delayed_events(
        context: AppContext, household_id: str, viewer_id: str | None
    ) -> AsyncIterator[ServerSentEvent]:
        # Stands in for the moment between the handler checking the token and the generator
        # subscribing, which is when the removal used to slip through.
        await removed.wait()
        async for event in original(context, household_id, viewer_id):
            yield event

    monkeypatch.setattr(stream_module, "_events", delayed_events)
    async with live.stream(live_home.id, token) as events:
        response = await live.client.delete(
            f"{live_home.url}/caregivers/{caregiver['id']}", headers=live_home.owner
        )
        assert response.status_code == 204
        removed.set()
        await live.client.patch(live_home.url, json={"name": "Renamed"}, headers=live_home.owner)
        await live_home.start_outage()
        assert await messages_until_closed(events) == []


async def test_removed_caregiver_stream_ends_without_the_new_join_code(
    live: LiveServer, live_home: Home
) -> None:
    caregiver, token = await live_home.join("Ex")
    async with live.stream(live_home.id, token) as events:
        assert (await events.message()).event == "snapshot"
        await live.client.delete(
            f"{live_home.url}/caregivers/{caregiver['id']}", headers=live_home.owner
        )
        # The removal replaces the join code; the removed caregiver must not be told it.
        assert await messages_until_closed(events) == []
    response = await live.client.get(live_home.url, headers=live_home.owner)
    assert response.json()["household"]["join_code"] != live_home.join_code
