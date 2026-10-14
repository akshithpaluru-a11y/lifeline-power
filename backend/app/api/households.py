"""Households: create, read, change, delete, and rotate the caregiver join code."""

from fastapi import APIRouter, status

from app.api.deps import HouseholdServiceDep, MemberAccess, OwnerAccess
from app.schemas import (
    HouseholdCreate,
    HouseholdCreatedResponse,
    HouseholdResponse,
    HouseholdUpdate,
    JoinCodeResponse,
)
from app.services.households import household_view

router = APIRouter(tags=["households"])


@router.post("/households", status_code=status.HTTP_201_CREATED)
async def create_household(
    body: HouseholdCreate, service: HouseholdServiceDep
) -> HouseholdCreatedResponse:
    """Create a household. The owner token is returned only here: keep it safe."""
    household, owner_token = await service.create(body)
    return HouseholdCreatedResponse(household=household_view(household), owner_token=owner_token)


@router.get("/households/{household_id}")
async def get_household(access: MemberAccess) -> HouseholdResponse:
    """The household with its devices, backups and caregivers."""
    return HouseholdResponse(household=household_view(access.household))


@router.patch("/households/{household_id}")
async def update_household(
    body: HouseholdUpdate, access: OwnerAccess, service: HouseholdServiceDep
) -> HouseholdResponse:
    """Change any of the household's own fields."""
    await service.update(access.household, body)
    return HouseholdResponse(household=household_view(access.household))


@router.delete("/households/{household_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_household(access: OwnerAccess, service: HouseholdServiceDep) -> None:
    """Delete the household and everything about it, including outage history."""
    await service.delete(access.household)


@router.post("/households/{household_id}/join-code/rotate")
async def rotate_join_code(access: OwnerAccess, service: HouseholdServiceDep) -> JoinCodeResponse:
    """Replace the join code (caregivers who already joined keep their access)."""
    return JoinCodeResponse(join_code=await service.rotate_join_code(access.household))
