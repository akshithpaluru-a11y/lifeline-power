"""Shared fixtures: an app on a temporary database, a controllable clock, mocked map services."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi import FastAPI

from app.config import Settings
from app.context import AppContext
from app.main import create_app
from app.ticker import OutageTicker
from tests.support import CENSUS_URL, NOMINATIM_URL, OVERPASS_URL, FakeClock, Home, create_home


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db=tmp_path / "test.db",
        ticker_enabled=False,
        anthropic_api_key=None,
        overpass_url=OVERPASS_URL,
        nominatim_url=NOMINATIM_URL,
        census_geocoder_url=CENSUS_URL,
        sse_ping_seconds=15,
    )


@pytest.fixture
def external() -> Iterator[respx.MockRouter]:
    """Mocked map services. By default they are down; tests replace the named routes."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.post(OVERPASS_URL, name="overpass").respond(503)
        router.get(url__startswith=f"{NOMINATIM_URL}/", name="nominatim").respond(503)
        router.get(url__startswith=f"{CENSUS_URL}/", name="census").respond(503)
        yield router


@pytest.fixture
async def app(
    settings: Settings, clock: FakeClock, external: respx.MockRouter
) -> AsyncIterator[FastAPI]:
    application = create_app(settings, clock=clock)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def context(app: FastAPI) -> AppContext:
    context: AppContext = app.state.context
    return context


@pytest.fixture
def ticker(app: FastAPI) -> OutageTicker:
    ticker: OutageTicker = app.state.ticker
    return ticker


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
async def home(client: httpx.AsyncClient) -> Home:
    """A household in Omaha (location given, so no lookups happen)."""
    return await create_home(client, lat=41.2565, lon=-95.9345, state="NE", utility_name="OPPD")
