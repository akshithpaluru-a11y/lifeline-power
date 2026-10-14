"""Bearer tokens for owners and caregivers.

There are no accounts or passwords. Creating a household returns an owner token; joining with a
household's join code returns a caregiver token. Tokens are 256-bit random strings, and only
their SHA-256 hashes are stored, so a leaked database doesn't leak working tokens. A prefix
(`llo_` / `llc_`) says which kind a token is, which keeps lookups to a single indexed query.
"""

import hashlib
import secrets
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.errors import forbidden, unauthorized

Role = Literal["owner", "caregiver"]
_PREFIX: dict[Role, str] = {"owner": "llo_", "caregiver": "llc_"}


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making a request."""

    role: Role
    household_id: str
    caregiver_id: str | None = None
    caregiver_name: str | None = None


def new_token(role: Role) -> str:
    """A fresh random token for an owner or caregiver."""
    return _PREFIX[role] + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """The SHA-256 hex digest stored in place of a token."""
    return hashlib.sha256(token.encode()).hexdigest()


async def resolve_token(session: AsyncSession, token: str | None) -> Principal:
    """Look up who a token belongs to. Raises 401 if it's missing or unknown."""
    if not token:
        raise unauthorized("Send your token as 'Authorization: Bearer <token>'.")
    digest = hash_token(token)
    if token.startswith(_PREFIX["owner"]):
        household_id = await session.scalar(
            select(models.Household.id).where(models.Household.owner_token_hash == digest)
        )
        if household_id is not None:
            return Principal(role="owner", household_id=household_id)
    elif token.startswith(_PREFIX["caregiver"]):
        caregiver = await session.scalar(
            select(models.Caregiver).where(models.Caregiver.token_hash == digest)
        )
        if caregiver is not None:
            return Principal(
                role="caregiver",
                household_id=caregiver.household_id,
                caregiver_id=caregiver.id,
                caregiver_name=caregiver.name,
            )
    raise unauthorized("This token isn't valid (it may have been revoked).")


def authorize(principal: Principal, household_id: str, *, require: Role | None = None) -> None:
    """Check a principal may act on a household, optionally only in a given role (403 if not)."""
    if principal.household_id != household_id:
        raise forbidden("This token is for a different household.")
    if require == "owner" and principal.role != "owner":
        raise forbidden("Only the household's owner can do this.")
    if require == "caregiver" and principal.role != "caregiver":
        raise forbidden("Only caregivers can do this.")
