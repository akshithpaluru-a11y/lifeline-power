"""The battery plan: how long every device can run, and how to stretch it."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ContextDep, MemberAccess
from app.schemas import PlanResponse
from app.services.households import compute_plan

router = APIRouter(tags=["plan"])


@router.get("/households/{household_id}/plan")
async def get_plan(
    access: MemberAccess,
    context: ContextDep,
    saver: Annotated[
        bool, Query(description="Preview the plan with every device in its saver setting.")
    ] = False,
) -> PlanResponse:
    """Compute the plan from the household's saved devices and backups."""
    return PlanResponse(
        plan=compute_plan(access.household, now=context.clock(), saver_preview=saver)
    )
