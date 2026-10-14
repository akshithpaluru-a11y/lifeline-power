"""The background ticker: announces outage milestones and missed check-ins as their time comes.

Every couple of seconds it looks at each active outage, marks milestones whose time has passed
and sends a `milestone` event for each, and sends `missed_check_in` when the owner is 15 minutes
(of outage time, so drills scale with `speed`) past a check-in.
"""

import asyncio
import contextlib
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app import models
from app.db import Database
from app.events import EventBroker, MilestoneData, MissedCheckInData
from app.outage_engine import mark_due, missed_check_in_minutes
from app.services.outages import OutageLocks, dump_milestones, load_milestones
from app.timeutil import Clock

logger = logging.getLogger(__name__)


class OutageTicker:
    """Runs `tick` every `interval_seconds` until stopped."""

    def __init__(
        self,
        db: Database,
        broker: EventBroker,
        locks: OutageLocks,
        clock: Clock,
        interval_seconds: float,
    ) -> None:
        self._db = db
        self._broker = broker
        self._locks = locks
        self._clock = clock
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start ticking in the background."""
        self._task = asyncio.create_task(self._run(), name="outage-ticker")

    async def stop(self) -> None:
        """Stop ticking and wait for the current tick to finish being cancelled."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def tick(self) -> None:
        """Check every active outage once."""
        now = self._clock()
        async with self._db.sessions() as session:
            active = (
                await session.execute(
                    select(models.Outage.id, models.Outage.household_id).where(
                        models.Outage.status == "active"
                    )
                )
            ).all()
        for outage_id, household_id in active:
            try:
                await self._advance(outage_id, household_id, now)
            except StaleDataError:
                # Changed or deleted by someone else at the same moment: try again next tick.
                logger.info("Outage %s changed during a tick; will retry", outage_id)

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # keep ticking: one bad tick must not silence later alarms
                logger.exception("Outage ticker failed; retrying next tick")
            await asyncio.sleep(self._interval)

    async def _advance(self, outage_id: str, household_id: str, now: datetime) -> None:
        """Announce whatever has come due for one outage."""
        async with self._locks(household_id), self._db.sessions() as session:
            outage = await session.get(models.Outage, outage_id, populate_existing=True)
            if outage is None or outage.status != "active":
                return
            milestones, newly_passed = mark_due(load_milestones(outage), now)
            overdue = missed_check_in_minutes(
                milestones,
                outage.last_check_in_at,
                outage.speed,
                now,
                outage.missed_check_in_alerts,
            )
            if not newly_passed and overdue is None:
                return
            if newly_passed:
                outage.milestones = dump_milestones(milestones)
            if overdue is not None:
                outage.missed_check_in_alerts += 1
            await session.commit()

        for milestone in newly_passed:
            self._broker.publish(
                household_id,
                "milestone",
                MilestoneData(outage_id=outage_id, milestone=milestone, drill=outage.drill),
            )
        if overdue is not None:
            self._broker.publish(
                household_id,
                "missed_check_in",
                MissedCheckInData(
                    outage_id=outage_id, minutes_overdue=round(overdue, 1), drill=outage.drill
                ),
            )
