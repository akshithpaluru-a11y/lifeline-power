"""Live events for a household, as Server-Sent Events.

Each message is one `event:` line and one `data:` line of compact JSON. A `: ping` comment keeps
idle connections open through proxies.
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sse_starlette import EventSourceResponse, JSONServerSentEvent, ServerSentEvent

from app import models
from app.api.deps import BearerDep, ContextDep, SessionDep
from app.auth import authorize, resolve_token
from app.context import AppContext
from app.db import Database
from app.events import SnapshotData
from app.services.households import household_view
from app.services.outages import active_outage, outage_view

router = APIRouter(tags=["live events"])

_SEPARATOR = "\n"


@router.get(
    "/households/{household_id}/stream",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "An event stream."}},
)
async def stream(
    household_id: str,
    session: SessionDep,
    context: ContextDep,
    credentials: BearerDep,
    token: Annotated[
        str | None,
        Query(description="Owner or caregiver token (EventSource can't send headers)."),
    ] = None,
) -> EventSourceResponse:
    """Stream the household's events, starting with a `snapshot` of its current state."""
    principal = await resolve_token(
        session, token or (credentials.credentials if credentials else None)
    )
    authorize(principal, household_id)
    return EventSourceResponse(
        _events(context, household_id, principal.caregiver_id),
        ping=context.settings.sse_ping_seconds,
        sep=_SEPARATOR,
        ping_message_factory=lambda: ServerSentEvent(comment="ping", sep=_SEPARATOR),
    )


async def _events(
    context: AppContext, household_id: str, viewer_id: str | None
) -> AsyncGenerator[ServerSentEvent]:
    """The snapshot, then every event until the client leaves or the stream is closed.

    `viewer_id` is the caregiver watching (None for the owner).
    """
    # Subscribe before reading the snapshot so nothing that happens in between is missed. It
    # also closes a gap in revoking access: the token was checked in the handler, before this
    # generator started, so a caregiver removed in between would not have been reached by the
    # removal's `broker.close`. The snapshot read checks again that they are still a caregiver;
    # a removal committed after that check finds this subscription and closes it.
    with context.broker.subscribe(household_id, viewer_id) as subscription:
        # The read runs in its own task: if the client leaves mid-read, the stream is cancelled
        # but the read still finishes and releases its connection, instead of being cut off
        # inside the database driver.
        snapshot = await asyncio.shield(_snapshot(context.db, household_id, viewer_id))
        if snapshot is None or subscription.closed:
            return
        yield _message("snapshot", snapshot.model_dump(mode="json"))
        while (event := await subscription.next()) is not None:
            yield _message(event.name, event.data)


async def _snapshot(db: Database, household_id: str, viewer_id: str | None) -> SnapshotData | None:
    """The household and its active outage.

    None if the household is gone, or the viewer is no longer one of its caregivers.
    """
    async with db.sessions() as session:
        household = await session.get(models.Household, household_id)
        if household is None:
            return None
        # The caregivers are read after the household row, so if this read already sees what
        # a removal changed (such as the new join code it sets), it also sees the removal.
        if viewer_id is not None and all(c.id != viewer_id for c in household.caregivers):
            return None
        outage = await active_outage(session, household_id)
        return SnapshotData(
            household=household_view(household),
            outage=None if outage is None else outage_view(outage),
        )


def _message(name: str, data: dict[str, Any]) -> ServerSentEvent:
    """One SSE message. JSON escapes newlines, so `data` is always a single line."""
    return JSONServerSentEvent(data, event=name, sep=_SEPARATOR)
