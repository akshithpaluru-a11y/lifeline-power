"""FastAPI dependencies: database sessions, who is calling, and the services they use."""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import Principal, Role, authorize, confirm_still_member, resolve_token
from app.context import AppContext
from app.services.households import HouseholdService, load_household
from app.services.outages import OutageService


def get_context(request: Request) -> AppContext:
    """The app's shared services."""
    context: AppContext = request.app.state.context
    return context


ContextDep = Annotated[AppContext, Depends(get_context)]


async def get_session(context: ContextDep) -> AsyncIterator[AsyncSession]:
    """A database session for one request."""
    async with context.db.sessions() as session:
        yield session


# "function" scope closes the session as soon as the handler returns, before the response is
# sent, so a long-lived event stream doesn't hold a database connection open.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="The owner token from creating a household, or a caregiver token from joining.",
)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def get_principal(session: SessionDep, credentials: BearerDep) -> Principal:
    """Who is calling, from the `Authorization: Bearer` header (401 if missing or unknown)."""
    return await resolve_token(session, credentials.credentials if credentials else None)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


@dataclass(frozen=True, slots=True)
class Access:
    """A caller who may use the household in the URL, and that household."""

    principal: Principal
    household: models.Household


def _household_access(require: Role | None) -> Callable[..., Awaitable[Access]]:
    """A dependency that checks the caller may act on `{household_id}` and loads it."""

    async def dependency(household_id: str, principal: PrincipalDep, session: SessionDep) -> Access:
        authorize(principal, household_id, require=require)
        household = await load_household(session, household_id)
        confirm_still_member(principal, household)
        return Access(principal, household)

    return dependency


MemberAccess = Annotated[Access, Depends(_household_access(None))]
"""The household's owner or one of its caregivers."""
OwnerAccess = Annotated[Access, Depends(_household_access("owner"))]
"""Only the household's owner."""
CaregiverAccess = Annotated[Access, Depends(_household_access("caregiver"))]
"""Only one of the household's caregivers."""


def get_household_service(session: SessionDep, context: ContextDep) -> HouseholdService:
    """Household operations bound to this request's session."""
    return HouseholdService(session, context.broker, context.geocoder, context.clock)


def get_outage_service(session: SessionDep, context: ContextDep) -> OutageService:
    """Outage operations bound to this request's session."""
    return OutageService(session, context.broker, context.locks, context.help_sites, context.clock)


HouseholdServiceDep = Annotated[HouseholdService, Depends(get_household_service)]
OutageServiceDep = Annotated[OutageService, Depends(get_outage_service)]
