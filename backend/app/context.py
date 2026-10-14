"""The long-lived objects every request shares, created once when the app starts."""

from dataclasses import dataclass

from app.ai import DeviceIdentifier
from app.auth import JoinCodeGuard
from app.config import Settings
from app.db import Database
from app.events import EventBroker
from app.geocoding import Geocoder
from app.help_sites import HelpSiteFinder
from app.services.outages import OutageLocks
from app.timeutil import Clock


@dataclass(frozen=True, slots=True)
class AppContext:
    """Shared services, stored on `app.state.context` for the lifetime of the app."""

    settings: Settings
    db: Database
    broker: EventBroker
    locks: OutageLocks
    help_sites: HelpSiteFinder
    geocoder: Geocoder
    identifier: DeviceIdentifier
    join_guard: JoinCodeGuard
    clock: Clock
