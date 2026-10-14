"""Live events: an in-process publish/subscribe broker, one channel per household.

API handlers and the ticker publish events; each open SSE stream holds a subscription with its
own queue. Everything runs on the server's single event loop, so no locking is needed. (Running
several server processes would need a shared broker such as Redis; one process is plenty for
this app.)
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas import Ack, Household, Milestone, Outage

logger = logging.getLogger(__name__)

EventName = Literal[
    "snapshot",
    "outage_started",
    "milestone",
    "check_in",
    "missed_check_in",
    "ack",
    "outage_ended",
    "household_updated",
]


# Payloads, one per event name (see the table in docs/API.md).
class SnapshotData(BaseModel):
    """`snapshot`: sent once when a stream connects."""

    household: Household
    outage: Outage | None


class OutageEventData(BaseModel):
    """`outage_started` and `outage_ended`."""

    outage: Outage
    drill: bool


class MilestoneData(BaseModel):
    """`milestone`: a milestone's time has come."""

    outage_id: str
    milestone: Milestone
    drill: bool


class CheckInData(BaseModel):
    """`check_in`: the owner checked in; plan and milestones were recomputed."""

    outage: Outage


class MissedCheckInData(BaseModel):
    """`missed_check_in`: the owner is overdue for a check-in."""

    outage_id: str
    minutes_overdue: float
    drill: bool


class AckData(BaseModel):
    """`ack`: a caregiver acknowledged the outage."""

    outage_id: str
    ack: Ack


class HouseholdData(BaseModel):
    """`household_updated`: anything about the household changed."""

    household: Household


@dataclass(frozen=True, slots=True)
class Event:
    """One event, with its payload already converted to JSON-ready values."""

    name: EventName
    data: dict[str, Any]


class Subscription:
    """A stream's view of one household's events."""

    def __init__(self, queue_size: int, viewer_id: str | None) -> None:
        self.viewer_id = viewer_id
        """Who is watching (a caregiver id), so one person's streams can be closed."""
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=queue_size)

    async def next(self) -> Event | None:
        """Wait for the next event; None means the channel was closed."""
        return await self._queue.get()

    def deliver(self, event: Event | None) -> None:
        """Queue an event. A subscriber that has fallen far behind loses its oldest event."""
        if self._queue.full():
            self._queue.get_nowait()
            logger.warning("Event queue full; dropped the oldest event for a slow subscriber")
        self._queue.put_nowait(event)


class EventBroker:
    """Fans events out to every subscriber of a household."""

    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: defaultdict[str, set[Subscription]] = defaultdict(set)

    @contextmanager
    def subscribe(self, household_id: str, viewer_id: str | None = None) -> Iterator[Subscription]:
        """Receive a household's events for as long as the `with` block runs."""
        subscription = Subscription(self._queue_size, viewer_id)
        self._subscribers[household_id].add(subscription)
        try:
            yield subscription
        finally:
            subscribers = self._subscribers.get(household_id)
            if subscribers is not None:
                subscribers.discard(subscription)
                if not subscribers:
                    del self._subscribers[household_id]

    def publish(self, household_id: str, name: EventName, payload: BaseModel) -> None:
        """Send an event to everyone watching the household."""
        event = Event(name=name, data=payload.model_dump(mode="json"))
        for subscription in self._subscribers.get(household_id, ()):
            subscription.deliver(event)

    def close(self, household_id: str, viewer_id: str | None = None) -> None:
        """End a household's streams: all of them, or just one viewer's.

        Used when a household is deleted, or a caregiver is removed and must stop seeing it.
        """
        for subscription in self._subscribers.get(household_id, ()):
            if viewer_id is None or subscription.viewer_id == viewer_id:
                subscription.deliver(None)

    def subscriber_count(self, household_id: str) -> int:
        """How many streams are watching a household."""
        return len(self._subscribers.get(household_id, ()))
