"""Time helpers. All times in the service are timezone-aware UTC with millisecond precision."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import PlainSerializer


def utcnow() -> datetime:
    """Current UTC time, truncated to milliseconds so stored and returned values match exactly."""
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def to_iso(value: datetime) -> str:
    """Format a datetime as the API's ISO 8601 UTC string, e.g. `2026-10-20T18:04:11.250Z`."""
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def minutes(value: float) -> timedelta:
    """A timedelta of `value` minutes."""
    return timedelta(minutes=value)


def format_duration(total_minutes: float) -> str:
    """Human-friendly duration such as `6 h 12 min`, `45 min` or `0 min`."""
    whole = max(0, round(total_minutes))
    hours, mins = divmod(whole, 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


Clock = Callable[[], datetime]
"""Something that tells the time. The app uses `utcnow`; tests pass a clock they control."""

UtcDateTime = Annotated[datetime, PlainSerializer(to_iso, return_type=str, when_used="json")]
"""A datetime that serializes to the API's ISO 8601 `...Z` format in JSON."""
