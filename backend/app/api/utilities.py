"""Utility programs for customers who rely on powered medical equipment."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.schemas import MedicalProgramOut, MedicalProgramsResponse, StateCode
from app.utilities_data import GENERIC_ADVICE, find_programs

router = APIRouter(tags=["help and preparation"])


@router.get("/utilities/medical-programs")
async def medical_programs(
    state: Annotated[StateCode, Query(description="2-letter state, e.g. NE")] = None,
    utility: Annotated[str | None, Query(max_length=100, description="e.g. OPPD")] = None,
) -> MedicalProgramsResponse:
    """Verified utility medical-customer programs, plus advice that applies everywhere."""
    return MedicalProgramsResponse(
        programs=[MedicalProgramOut.model_validate(p) for p in find_programs(state, utility)],
        generic_advice=list(GENERIC_ADVICE),
    )
