"""The background ticker: milestone and missed-check-in events, at real and drill speed."""

import asyncio

import httpx
import pytest
import respx

from app.config import Settings
from app.context import AppContext
from app.main import create_app
from app.ticker import OutageTicker
from tests.support import FakeClock, Home, collect, create_home


@pytest.fixture
async def equipped(home: Home) -> Home:
    await home.add_device(kind="oxygen_concentrator_home", label="Oxygen")  # 145.7 min
    await home.add_backup(type="power_station")
    return home


async def test_milestones_are_announced_when_due(
    equipped: Home, clock: FakeClock, ticker: OutageTicker, context: AppContext
) -> None:
    outage = await equipped.start_outage()
    with context.broker.subscribe(equipped.id) as events:
        await ticker.tick()
        assert await collect(events) == []  # start milestones were announced with the outage

        clock.advance(minutes=73)  # past check_in (60) and halfway (72.9)
        await ticker.tick()
        first = await collect(events)
        await ticker.tick()
        again = await collect(events)

    assert [(e.name, e.data["milestone"]["key"]) for e in first] == [
        ("milestone", "check_in"),
        ("milestone", "halfway"),
    ]
    assert all(e.data["outage_id"] == outage["id"] and not e.data["drill"] for e in first)
    assert first[0].data["milestone"]["passed"] is True
    assert again == []


async def test_drill_at_speed_300_reaches_depleted_in_seconds(
    client: httpx.AsyncClient,
    equipped: Home,
    clock: FakeClock,
    ticker: OutageTicker,
    context: AppContext,
) -> None:
    await equipped.start_outage(source="drill", speed=300)
    with context.broker.subscribe(equipped.id) as events:
        clock.advance(seconds=30)  # 150 outage minutes
        await ticker.tick()
        published = await collect(events)
    keys = [e.data["milestone"]["key"] for e in published if e.name == "milestone"]
    assert keys == ["check_in", "halfway", "leave_by", "critical_low", "depleted"]
    assert all(e.data["drill"] is True for e in published)
    current = (await client.get(f"{equipped.url}/outages/current", headers=equipped.owner)).json()[
        "outage"
    ]
    assert all(m["passed"] for m in current["milestones"])


async def test_missed_check_in_alerts(
    client: httpx.AsyncClient,
    home: Home,
    clock: FakeClock,
    ticker: OutageTicker,
    context: AppContext,
) -> None:
    await home.add_device(kind="home_ventilator", spare_batteries_wh=950)  # a long countdown
    outage = await home.start_outage()
    with context.broker.subscribe(home.id) as events:
        clock.advance(minutes=74)  # check-in was due at 60
        await ticker.tick()
        assert [e.name for e in await collect(events) if e.name == "missed_check_in"] == []

        clock.advance(minutes=1)
        await ticker.tick()
        await ticker.tick()
        missed = [e for e in await collect(events) if e.name == "missed_check_in"]
        assert [e.data for e in missed] == [
            {"outage_id": outage["id"], "minutes_overdue": 15.0, "drill": False}
        ]

        clock.advance(minutes=15)
        await ticker.tick()
        missed = [e for e in await collect(events) if e.name == "missed_check_in"]
        assert [e.data["minutes_overdue"] for e in missed] == [30.0]

        await client.post(f"{home.url}/outages/current/check-in", headers=home.owner)
        clock.advance(minutes=30)
        await ticker.tick()
        assert not [e for e in await collect(events) if e.name == "missed_check_in"]


async def test_missed_check_in_scales_with_drill_speed(
    home: Home, clock: FakeClock, ticker: OutageTicker, context: AppContext
) -> None:
    await home.add_device(kind="home_ventilator", spare_batteries_wh=950)
    await home.start_outage(source="drill", speed=60)
    with context.broker.subscribe(home.id) as events:
        clock.advance(seconds=75)  # 75 outage minutes: check-in due at 60, so 15 overdue
        await ticker.tick()
        missed = [e for e in await collect(events) if e.name == "missed_check_in"]
    assert [(e.data["minutes_overdue"], e.data["drill"]) for e in missed] == [(15.0, True)]


async def test_ended_outages_are_left_alone(
    client: httpx.AsyncClient,
    equipped: Home,
    clock: FakeClock,
    ticker: OutageTicker,
    context: AppContext,
) -> None:
    await equipped.start_outage()
    await client.post(f"{equipped.url}/outages/current/end", headers=equipped.owner)
    with context.broker.subscribe(equipped.id) as events:
        clock.advance(hours=5)
        await ticker.tick()
        assert await collect(events) == []


async def test_ticker_runs_in_the_background(
    settings: Settings, clock: FakeClock, external: respx.MockRouter
) -> None:
    settings = settings.model_copy(update={"ticker_enabled": True, "ticker_interval_seconds": 0.01})
    app = create_app(settings, clock=clock)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            home = await create_home(client, lat=41.2565, lon=-95.9345, state="NE")
            await home.add_device(kind="oxygen_concentrator_home")
            await home.start_outage()
            context: AppContext = app.state.context
            with context.broker.subscribe(home.id) as events:
                clock.advance(hours=3)
                await asyncio.sleep(0.1)
                published = await collect(events)
    assert "depleted" in [e.data["milestone"]["key"] for e in published if e.name == "milestone"]
