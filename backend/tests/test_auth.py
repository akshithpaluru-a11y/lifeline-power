"""Auth rules: hashed tokens, owner vs caregiver, and one household per token."""

import httpx
from sqlalchemy import select

from app import models
from app.auth import hash_token
from app.context import AppContext
from tests.support import Home, bearer, create_home


async def test_only_token_hashes_are_stored(home: Home, context: AppContext) -> None:
    _, caregiver_token = await home.join()
    async with context.db.sessions() as session:
        household = await session.get(models.Household, home.id)
        caregiver = await session.scalar(select(models.Caregiver))
    assert household is not None and caregiver is not None
    assert household.owner_token_hash == hash_token(home.owner_token)
    assert home.owner_token not in household.owner_token_hash
    assert caregiver.token_hash == hash_token(caregiver_token)
    assert len(household.owner_token_hash) == 64


async def test_missing_or_unknown_token(client: httpx.AsyncClient, home: Home) -> None:
    for headers in ({}, bearer("llo_not-a-real-token"), {"Authorization": "Basic abc"}):
        response = await client.get(home.url, headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


async def test_token_only_works_for_its_own_household(
    client: httpx.AsyncClient, home: Home
) -> None:
    other = await create_home(client, name="Other home")
    response = await client.get(home.url, headers=other.owner)
    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "forbidden",
        "message": "This token is for a different household.",
    }


async def test_caregivers_read_but_do_not_change(client: httpx.AsyncClient, home: Home) -> None:
    _, token = await home.join()
    caregiver = bearer(token)
    for method, path in [
        ("GET", ""),
        ("GET", "/plan"),
        ("GET", "/outages/current"),
        ("GET", "/emergency-card"),
    ]:
        response = await client.request(method, f"{home.url}{path}", headers=caregiver)
        assert response.status_code == 200, path
    for method, path in [
        ("PATCH", ""),
        ("DELETE", ""),
        ("POST", "/join-code/rotate"),
        ("POST", "/outages"),
        ("GET", "/outages"),
        ("POST", "/outages/current/check-in"),
        ("POST", "/outages/current/end"),
        ("GET", "/medical-letter"),
    ]:
        response = await client.request(method, f"{home.url}{path}", headers=caregiver, json={})
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "forbidden"


async def test_owner_cannot_acknowledge(client: httpx.AsyncClient, home: Home) -> None:
    await home.start_outage()
    response = await client.post(f"{home.url}/outages/current/ack", json={}, headers=home.owner)
    assert response.status_code == 403
