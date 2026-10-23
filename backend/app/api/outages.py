"""Outages and drills: start, follow, check in, acknowledge, end, and history."""

from typing import Annotated

from fastapi import APIRouter, Body, status

from app.api.deps import CaregiverAccess, MemberAccess, OutageServiceDep, OwnerAccess
from app.schemas import (
    AckRequest,
    CheckInRequest,
    OutageCreate,
    OutageHistoryResponse,
    OutageResponse,
)

router = APIRouter(tags=["outages"])


@router.post("/households/{household_id}/outages", status_code=status.HTTP_201_CREATED)
async def start_outage(
    body: OutageCreate, access: OwnerAccess, service: OutageServiceDep
) -> OutageResponse:
    """Start an outage (or a drill). 409 if one is already active."""
    return OutageResponse(outage=await service.start(access.household, body))


@router.get("/households/{household_id}/outages/current")
async def current_outage(access: MemberAccess, service: OutageServiceDep) -> OutageResponse:
    """The active outage, or `null`."""
    return OutageResponse(outage=await service.current(access.household.id))


@router.post("/households/{household_id}/outages/current/check-in")
async def check_in(
    access: OwnerAccess,
    service: OutageServiceDep,
    body: Annotated[CheckInRequest | None, Body()] = None,
) -> OutageResponse:
    """The owner says how they are, optionally with battery levels; the plan is recomputed."""
    outage = await service.check_in(access.household, body or CheckInRequest())
    return OutageResponse(outage=outage)


@router.post("/households/{household_id}/outages/current/ack")
async def acknowledge(
    access: CaregiverAccess,
    service: OutageServiceDep,
    body: Annotated[AckRequest | None, Body()] = None,
) -> OutageResponse:
    """A caregiver acknowledges the outage, optionally with a message and arrival time."""
    outage = await service.ack(access.household.id, access.principal, body or AckRequest())
    return OutageResponse(outage=outage)


@router.post("/households/{household_id}/outages/current/end")
async def end_outage(access: OwnerAccess, service: OutageServiceDep) -> OutageResponse:
    """End the active outage."""
    return OutageResponse(outage=await service.end(access.household.id))


@router.get("/households/{household_id}/outages")
async def outage_history(access: OwnerAccess, service: OutageServiceDep) -> OutageHistoryResponse:
    """Past and current outages, newest first, without their plans."""
    return OutageHistoryResponse(outages=await service.history(access.household.id))
