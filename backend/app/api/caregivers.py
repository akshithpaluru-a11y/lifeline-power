"""Caregivers: join a household with its code, or be removed by the owner."""

from fastapi import APIRouter, Request, status

from app.api.deps import ContextDep, HouseholdServiceDep, OwnerAccess
from app.auth import client_key
from app.schemas import Caregiver, CaregiverJoinedResponse, JoinRequest

router = APIRouter(tags=["caregivers"])


@router.post(
    "/caregivers/join",
    status_code=status.HTTP_201_CREATED,
    responses={429: {"description": "Too many wrong join codes recently; try again later."}},
)
async def join_household(
    body: JoinRequest, request: Request, service: HouseholdServiceDep, context: ContextDep
) -> CaregiverJoinedResponse:
    """Join with the household's 6-character code. Returns a read-only caregiver token.

    Too many wrong codes from one address (or from everyone together) get `429` for a while,
    so codes can't be guessed.
    """
    attempt = context.join_guard.begin(client_key(request.client.host if request.client else None))
    caregiver, token, household = await service.join(body)
    context.join_guard.succeeded(attempt)
    return CaregiverJoinedResponse(
        caregiver=Caregiver.model_validate(caregiver), caregiver_token=token, household=household
    )


@router.delete(
    "/households/{household_id}/caregivers/{caregiver_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_caregiver(
    caregiver_id: str, access: OwnerAccess, service: HouseholdServiceDep
) -> None:
    """Remove a caregiver. Their token stops working and their live stream closes.

    The join code is replaced at the same time (they have seen it), so share the new one with
    anyone who still needs to join.
    """
    await service.remove_caregiver(access.household, caregiver_id)
