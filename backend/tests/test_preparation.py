"""Utility programs, the medical letter and the emergency card."""

import httpx

from app.utilities_data import PROGRAMS, VERIFIED_AT, find_programs
from tests.support import Home, bearer


def test_every_program_is_verified_and_links_to_https() -> None:
    assert 12 <= len(PROGRAMS) <= 20
    for program in PROGRAMS:
        assert program.url.startswith("https://")
        assert program.verified_at == VERIFIED_AT
        assert program.states and all(len(state) == 2 for state in program.states)


def test_find_programs_by_state_and_utility() -> None:
    assert [p.utility for p in find_programs(utility="PG&E")] == ["Pacific Gas and Electric (PG&E)"]
    assert {p.program_name for p in find_programs(state="CA")} >= {
        p.program_name for p in find_programs(utility="SDG&E")
    }
    assert find_programs(state="NE", utility="OPPD")[0].states == ("NE",)
    assert find_programs(state="IA", utility="OPPD") == []  # OPPD doesn't serve Iowa


def test_utility_names_do_not_match_the_wrong_company() -> None:
    assert [p.utility for p in find_programs(utility="Con Edison")] == ["Con Edison"]
    assert find_programs(utility="PSEG Long Island") == []


async def test_medical_programs_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/utilities/medical-programs", params={"state": "ne"})
    body = response.json()
    assert response.status_code == 200
    [program] = body["programs"]
    assert set(program) == {
        "utility",
        "states",
        "program_name",
        "what_it_does",
        "how_to_apply",
        "url",
        "verified_at",
    }
    assert program["verified_at"] == "2026-10-14"
    assert len(body["generic_advice"]) >= 5

    everything = await client.get("/api/utilities/medical-programs")
    assert len(everything.json()["programs"]) == len(PROGRAMS)
    response = await client.get("/api/utilities/medical-programs", params={"state": "XX"})
    assert response.status_code == 400


async def test_medical_letter(client: httpx.AsyncClient, home: Home) -> None:
    await home.add_device(kind="home_ventilator", label="Dad's ventilator")
    response = await client.get(f"{home.url}/medical-letter", headers=home.owner)
    assert response.status_code == 200
    letter = response.json()
    assert set(letter) == {"subject", "body"}
    body = letter["body"]
    assert body.startswith("October 20, 2026")
    assert "To: OPPD" in body
    assert "Dad's ventilator (life-sustaining): about 20 W" in body
    assert "CLINICIAN CERTIFICATION" in body
    assert "Signature: ______" in body
    assert "https://oppd.com/" in body  # OPPD's own program page

    _, token = await home.join()
    response = await client.get(f"{home.url}/medical-letter", headers=bearer(token))
    assert response.status_code == 403


async def test_emergency_card(client: httpx.AsyncClient, home: Home) -> None:
    await home.add_device(kind="home_ventilator", label="Dad's ventilator")
    await home.add_backup(type="generator")
    await home.join("Maya", relationship="Granddaughter")
    response = await client.get(f"{home.url}/emergency-card", headers=home.owner)
    card = response.json()
    assert card["patient_first_name"] == "Luis"
    assert card["devices"] == [
        {
            "label": "Dad's ventilator",
            "life_sustaining": True,
            "draw_watts": 20,
            "internal_minutes": 256.5,
        }
    ]
    assert card["critical_runtime_minutes"] == 256.5 + 480
    assert card["caregivers"] == [{"name": "Maya", "relationship": "Granddaughter"}]
    assert card["utility_name"] == "OPPD"
    assert any("outdoors" in step for step in card["instructions"])
    assert any("911" in step for step in card["instructions"])
