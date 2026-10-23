"""HTTP routes, all under `/api` (see docs/API.md)."""

from fastapi import APIRouter

from app.api import (
    ai,
    caregivers,
    catalog,
    equipment,
    households,
    outages,
    plan,
    preparation,
    stream,
    utilities,
)

api_router = APIRouter(prefix="/api")
for module in (
    catalog,
    households,
    equipment,
    plan,
    outages,
    caregivers,
    stream,
    preparation,
    utilities,
    ai,
):
    api_router.include_router(module.router)
