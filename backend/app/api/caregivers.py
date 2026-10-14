"""Caregivers: join a household with its code, or be removed by the owner."""

from fastapi import APIRouter, status

from app.api.deps import HouseholdServiceDep, OwnerAccess
from app.schemas import Caregiver, CaregiverJoinedResponse, JoinRequest
from app.services.households import household_view

router = APIRouter(tags=["caregivers"])


@router.post("/caregivers/join", status_code=status.HTTP_201_CREATED)
async def join_household(
    body: JoinRequest, service: HouseholdServiceDep
) -> CaregiverJoinedResponse:
    """Join with the household's 6-character code. Returns a read-only caregiver token."""
    caregiver, token, household = await service.join(body)
    return CaregiverJoinedResponse(
        caregiver=Caregiver.model_validate(caregiver),
        caregiver_token=token,
        household=household_view(household),
    )


@router.delete(
    "/households/{household_id}/caregivers/{caregiver_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_caregiver(
    caregiver_id: str, access: OwnerAccess, service: HouseholdServiceDep
) -> None:
    """Remove a caregiver. Their token stops working and their live stream closes."""
    await service.remove_caregiver(access.household, caregiver_id)
