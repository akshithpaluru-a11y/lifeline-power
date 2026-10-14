"""Help and preparation: nearby places with power, the utility letter and the emergency card."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ContextDep, MemberAccess, OwnerAccess
from app.errors import bad_request
from app.help_sites import NO_LOCATION_NOTE
from app.letters import emergency_card, medical_letter
from app.schemas import EmergencyCard, HelpSitesResponse, MedicalLetter
from app.services.households import compute_plan, household_view
from app.utilities_data import find_programs

router = APIRouter(tags=["help and preparation"])


@router.get("/households/{household_id}/help-sites")
async def help_sites(
    access: MemberAccess,
    context: ContextDep,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(ge=1, le=50)] = 15,
) -> HelpSitesResponse:
    """Hospitals, fire stations and other places nearby that may have power.

    `lat` / `lon` default to the household's location (for example, pass the phone's current
    position when away from home).
    """
    if (lat is None) != (lon is None):
        raise bad_request("lat and lon must be given together.")
    if lat is None or lon is None:
        lat, lon = access.household.lat, access.household.lon
    if lat is None or lon is None:
        return HelpSitesResponse(sites=[], source="fallback", note=NO_LOCATION_NOTE)
    result = await context.help_sites.find(lat, lon, radius_km)
    return HelpSitesResponse(sites=result.sites, source=result.source, note=result.note)


@router.get("/households/{household_id}/medical-letter")
async def get_medical_letter(access: OwnerAccess, context: ContextDep) -> MedicalLetter:
    """A letter asking the utility to register the home as a medical / life-support customer."""
    household = access.household
    programs = find_programs(household.state, household.utility_name)
    program = programs[0] if household.utility_name and programs else None
    now = context.clock()
    return medical_letter(
        household_view(household), compute_plan(household, now=now), program, now.date()
    )


@router.get("/households/{household_id}/emergency-card")
async def get_emergency_card(access: MemberAccess, context: ContextDep) -> EmergencyCard:
    """A one-page summary for the fridge door or first responders."""
    plan = compute_plan(access.household, now=context.clock())
    return emergency_card(household_view(access.household), plan)
