"""The FastAPI application: `uvicorn app.main:app`.

`create_app` wires everything together. The lifespan opens the database and the shared HTTP
client, builds the services every request uses, and runs the outage ticker until shutdown.
"""

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import anthropic
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.ai import DeviceIdentifier
from app.api import api_router
from app.auth import JoinCodeGuard
from app.config import Settings, get_settings
from app.context import AppContext
from app.db import Database
from app.errors import install_error_handlers
from app.events import EventBroker
from app.geocoding import Geocoder
from app.help_sites import HelpSiteFinder
from app.services.outages import OutageLocks
from app.ticker import OutageTicker
from app.timeutil import Clock, utcnow

_DESCRIPTION = """\
Turns a plugged-in phone into a power-outage alarm for home medical devices: battery runtime
planning, a live outage countdown, caregiver alerts and nearby places with power.

Authenticate with the owner token from `POST /api/households` or a caregiver token from
`POST /api/caregivers/join` (the **Authorize** button).
"""


class _RedactTokens(logging.Filter):
    """Keep `?token=` values (used by the event stream) out of the access log."""

    _TOKEN = re.compile(r"(token=)[^&\s]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                self._TOKEN.sub(r"\1[redacted]", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


_REDACT_TOKENS = _RedactTokens()


def create_app(settings: Settings | None = None, *, clock: Clock = utcnow) -> FastAPI:
    """Build the app. Tests pass their own settings and a clock they control."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.db.parent.mkdir(parents=True, exist_ok=True)
        db = Database(settings.database_url)
        await db.create_tables()
        claude = (
            anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key,
                timeout=settings.ai_timeout_seconds,
                max_retries=1,
            )
            if settings.anthropic_api_key
            else None
        )
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent}, timeout=settings.http_timeout_seconds
        ) as http:
            context = AppContext(
                settings=settings,
                db=db,
                broker=EventBroker(),
                locks=OutageLocks(),
                help_sites=HelpSiteFinder(
                    http, settings.overpass_url, timeout_seconds=settings.http_timeout_seconds
                ),
                geocoder=Geocoder(
                    http,
                    census_url=settings.census_geocoder_url,
                    nominatim_url=settings.nominatim_url,
                    timeout_seconds=settings.http_timeout_seconds,
                ),
                identifier=DeviceIdentifier(claude, settings.anthropic_model),
                join_guard=JoinCodeGuard(
                    clock,
                    per_client=settings.join_attempts_per_client,
                    overall=settings.join_attempts_overall,
                    window=timedelta(seconds=settings.join_attempt_window_seconds),
                ),
                clock=clock,
            )
            app.state.context = context
            app.state.ticker = ticker = OutageTicker(
                db, context.broker, context.locks, clock, settings.ticker_interval_seconds
            )
            if settings.ticker_enabled:
                ticker.start()
            try:
                yield
            finally:
                await ticker.stop()
                if claude is not None:
                    await claude.close()
                await db.dispose()

    app = FastAPI(
        title="LifeLine Power API",
        version=__version__,
        description=_DESCRIPTION,
        lifespan=lifespan,
    )
    # Auth uses bearer tokens, not cookies, so allowing any origin exposes nothing extra.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(api_router)
    logging.getLogger("uvicorn.access").addFilter(_REDACT_TOKENS)
    return app


app = create_app()
