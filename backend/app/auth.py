"""Bearer tokens for owners and caregivers.

There are no accounts or passwords. Creating a household returns an owner token; joining with a
household's join code returns a caregiver token. Tokens are 256-bit random strings, and only
their SHA-256 hashes are stored, so a leaked database doesn't leak working tokens. A prefix
(`llo_` / `llc_`) says which kind a token is, which keeps lookups to a single indexed query.

Join codes are short so that people can read them out, which makes them guessable in principle;
`JoinCodeGuard` limits how fast anyone can try.
"""

import hashlib
import ipaddress
import logging
import math
import secrets
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.errors import ApiError, forbidden, unauthorized
from app.timeutil import Clock

logger = logging.getLogger(__name__)

Role = Literal["owner", "caregiver"]
_PREFIX: dict[Role, str] = {"owner": "llo_", "caregiver": "llc_"}
_REVOKED = "This token isn't valid (it may have been revoked)."


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
    raise unauthorized(_REVOKED)


def authorize(principal: Principal, household_id: str, *, require: Role | None = None) -> None:
    """Check a principal may act on a household, optionally only in a given role (403 if not)."""
    if principal.household_id != household_id:
        raise forbidden("This token is for a different household.")
    if require == "owner" and principal.role != "owner":
        raise forbidden("Only the household's owner can do this.")
    if require == "caregiver" and principal.role != "caregiver":
        raise forbidden("Only caregivers can do this.")


def confirm_still_member(principal: Principal, household: models.Household) -> None:
    """Check a caregiver is still one of the household's caregivers (401 if not).

    The token was checked a moment earlier, so this only matters when the caregiver was removed
    in between: without it, the request could still read the household, including the join
    code that the removal has just replaced.
    """
    if principal.role == "caregiver" and all(
        caregiver.id != principal.caregiver_id for caregiver in household.caregivers
    ):
        raise unauthorized(_REVOKED)


# --- Join-code guessing ------------------------------------------------------------------


def client_key(host: str | None) -> str:
    """Who is making a request, for `JoinCodeGuard`: an IP address, or an IPv6 /56 network.

    A home or an attacker usually has a whole IPv6 /56 (ISPs hand out /56 or /48 blocks), so
    counting each address or /64 in it on its own would let guesses rotate through them.
    """
    if not host:
        return "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network((address, 56), strict=False))
    return str(address)


@dataclass(slots=True)
class JoinAttempt:
    """One try at joining with a join code. It counts as wrong unless it succeeds."""

    client: str
    at: datetime
    wrong: bool = True


class JoinCodeGuard:
    """Slows down anyone trying to guess join codes.

    A join code is 6 characters from a 31-character alphabet, about 887 million codes. Guessed
    at full speed, codes would sooner or later hit some family's household, exposing its
    address, its medical devices and when its power is out. So wrong codes are limited per
    client (see `client_key`) and for everyone together, over a sliding window. Families don't
    notice: a successful join doesn't count against anyone, and a few typos are fine.

    An attempt counts as wrong from the moment it starts until it succeeds, so a burst of
    parallel guesses can't all get through before any of them has failed. While a limit is
    reached, attempts are refused without looking at the code at all, except that a client with
    no wrong codes in the window still gets one try while only the overall limit is reached:
    otherwise anyone could pause joining for every family by sending wrong codes from many
    addresses. Guesses per window then stay bounded by the number of distinct clients.

    The counts live in memory, like the event broker's subscriptions: one server process.
    """

    def __init__(self, clock: Clock, *, per_client: int, overall: int, window: timedelta) -> None:
        self._clock = clock
        self._per_client = per_client
        self._overall = overall
        self._window = window
        self._attempts: deque[JoinAttempt] = deque()
        """Attempts within the window, oldest first."""
        self._wrong_by_client: Counter[str] = Counter()
        self._wrong_total = 0

    def begin(self, client: str) -> JoinAttempt:
        """Count an attempt by `client` before its code is checked (429 if they must wait)."""
        now = self._clock()
        self._forget_before(now - self._window)
        if self._wrong_by_client[client] >= self._per_client:
            logger.warning("Refused a join attempt from %s: too many wrong join codes", client)
            minutes = max(1, math.ceil(self._window.total_seconds() / 60))
            raise _too_many_attempts(
                f"Too many tries with a wrong join code. Wait {minutes} minutes, then check "
                "the code with the family."
            )
        if self._wrong_total >= self._overall and self._wrong_by_client[client] > 0:
            logger.warning("Refused a join attempt: too many wrong join codes from everyone")
            raise _too_many_attempts(
                "Many wrong join codes have been tried recently, so joining is limited for now. "
                "Check the code with the family, then try again later."
            )
        attempt = JoinAttempt(client, now)
        self._attempts.append(attempt)
        self._wrong_by_client[client] += 1
        self._wrong_total += 1
        return attempt

    def succeeded(self, attempt: JoinAttempt) -> None:
        """The attempt's code was right, so it no longer counts against anyone."""
        if attempt.wrong:
            attempt.wrong = False
            self._uncount(attempt.client)

    def _forget_before(self, cutoff: datetime) -> None:
        while self._attempts and self._attempts[0].at <= cutoff:
            attempt = self._attempts.popleft()
            if attempt.wrong:
                self._uncount(attempt.client)

    def _uncount(self, client: str) -> None:
        self._wrong_total -= 1
        self._wrong_by_client[client] -= 1
        if self._wrong_by_client[client] <= 0:
            del self._wrong_by_client[client]


def _too_many_attempts(message: str) -> ApiError:
    """429 with the contract's `unavailable` code: joining is unavailable to this caller now."""
    return ApiError(429, "unavailable", message)
