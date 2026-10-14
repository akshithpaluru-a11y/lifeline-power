"""Regression tests for utility programs and printed documents: a family is never pointed at
another state's or another company's program, and the emergency card says what its runtime
really assumes."""

import httpx
import pytest

from app.schemas import US_STATE_CODES
from app.utilities_data import PROGRAMS, find_programs
from tests.support import create_home

# --- Findings 30 and 46: sister companies and unknown states ------------------------------------


@pytest.mark.parametrize(
    "utility",
    [
        "Ameren Illinois",
        "National Grid",
        "National Grid Massachusetts",
        "Dominion Energy",
        "Dominion Energy South Carolina",
        "CenterPoint Energy",
        "CenterPoint Energy Indiana South",
        "PacifiCorp",
        "PSEG",
        "PSE&G",
        "Tacoma City Light",
    ],
)
def test_ambiguous_or_sister_company_names_match_nothing_without_a_state(utility: str) -> None:
    assert find_programs(None, utility) == []


@pytest.mark.parametrize(
    ("state", "utility"),
    [
        ("IL", "Ameren Illinois"),
        ("IL", "Ameren"),
        ("MA", "National Grid"),
        ("SC", "Dominion Energy South Carolina"),
        ("IN", "CenterPoint Energy Indiana South"),
        ("UT", "PacifiCorp"),
        ("WA", "Tacoma City Light"),
        ("WA", "Tacoma Power"),
    ],
)
def test_other_companies_in_a_known_state_match_nothing(state: str, utility: str) -> None:
    assert find_programs(state, utility) == []


@pytest.mark.parametrize(
    ("state", "utility", "expected"),
    [
        ("NY", "National Grid", "National Grid (Upstate New York)"),
        ("NY", "National Grid Upstate NY", "National Grid (Upstate New York)"),
        (None, "Niagara Mohawk", "National Grid (Upstate New York)"),
        ("MO", "Ameren", "Ameren Missouri"),
        (None, "Ameren Missouri", "Ameren Missouri"),
        ("VA", "Dominion Energy", "Dominion Energy Virginia"),
        (None, "Dominion Energy Virginia", "Dominion Energy Virginia"),
        ("TX", "CenterPoint Energy", "CenterPoint Energy (Houston electric)"),
        (None, "CenterPoint Energy Houston Electric", "CenterPoint Energy (Houston electric)"),
        ("WA", "City Light", "Seattle City Light"),
        (None, "Seattle City Light", "Seattle City Light"),
        ("OR", "PacifiCorp", "Pacific Power"),
        ("NJ", "PSEG", "Public Service Electric and Gas (PSE&G)"),
        (None, "PSEG New Jersey", "Public Service Electric and Gas (PSE&G)"),
        (None, "Duke Energy Progress NC", "Duke Energy"),
        (None, "Entergy Louisiana, LLC", "Entergy"),
        (None, "OPPD", "Omaha Public Power District (OPPD)"),
    ],
)
def test_the_right_program_is_still_found(state: str | None, utility: str, expected: str) -> None:
    assert [p.utility for p in find_programs(state, utility)] == [expected]


def test_no_name_ever_points_at_a_program_for_another_state() -> None:
    for program in PROGRAMS:
        for name in (program.utility, *program.aliases, *program.shared_names):
            for state in sorted(US_STATE_CODES):
                for found in find_programs(state, name):
                    assert state in found.states, (name, state, found.utility)


def test_without_a_state_only_names_unique_to_one_company_match() -> None:
    # A name that two programs' companies share would point one of them at the wrong program.
    for program in PROGRAMS:
        for name in (program.utility, *program.aliases):
            assert [p.utility for p in find_programs(None, name)] == [program.utility], name


async def test_letter_for_a_sister_company_without_a_state_cites_no_program(
    client: httpx.AsyncClient,
) -> None:
    home = await create_home(client, utility_name="Ameren Illinois")
    await home.add_device(kind="home_ventilator", label="Vent")
    body = (await client.get(f"{home.url}/medical-letter", headers=home.owner)).json()["body"]
    assert "To: Ameren Illinois" in body
    assert "Medical Equipment Registry" not in body
    assert "ameren.com" not in body

    plan = (await client.get(f"{home.url}/plan", headers=home.owner)).json()["plan"]
    [step] = [r for r in plan["recommendations"] if r["kind"] == "register_program"]
    assert step["title"] == "Register with Ameren Illinois as a medical customer"


async def test_programs_endpoint_needs_the_state_for_a_shared_name(
    client: httpx.AsyncClient,
) -> None:
    url = "/api/utilities/medical-programs"
    alone = await client.get(url, params={"utility": "National Grid"})
    assert alone.json()["programs"] == []
    with_state = await client.get(url, params={"utility": "National Grid", "state": "NY"})
    assert [p["utility"] for p in with_state.json()["programs"]] == [
        "National Grid (Upstate New York)"
    ]


# --- Findings 10 and 32: the emergency card's runtime line --------------------------------------


async def test_card_runtime_line_says_it_uses_the_saved_charge(client: httpx.AsyncClient) -> None:
    home = await create_home(client)
    await home.add_device(kind="home_ventilator", label="Vent", internal_battery_wh=0)
    await home.add_backup(type="power_station", charge_percent=40)
    card = (await client.get(f"{home.url}/emergency-card", headers=home.owner)).json()
    text = " ".join(card["instructions"])
    assert "full batteries" not in text
    assert "charge levels last saved" in text
    # 1000 Wh x 40% x 0.85 (inverter) / 20 W = 17 h, the figure the card shows elsewhere.
    assert card["critical_runtime_minutes"] == 1020
    assert "Vent lasts about 17 h." in text


async def test_card_with_no_backup_power_says_to_act_at_once(client: httpx.AsyncClient) -> None:
    home = await create_home(client)
    await home.add_device(kind="home_ventilator", label="Vent", charge_percent=0)
    card = (await client.get(f"{home.url}/emergency-card", headers=home.owner)).json()
    assert card["critical_runtime_minutes"] == 0
    text = " ".join(card["instructions"])
    assert "0 min" not in text
    assert "well before then" not in text
    assert "Vent has no battery or backup power" in text
    assert "right away" in text
