"""Random identifiers and join codes."""

import secrets
import string

_ID_ALPHABET = string.ascii_letters + string.digits
# Join codes are read aloud and typed on phones, so leave out look-alikes (0/O, 1/I/L).
_JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 6


def new_id(prefix: str, length: int = 10) -> str:
    """A short random URL-safe id such as `hh_a81KdQ0zXy` (about 59 bits of randomness)."""
    return f"{prefix}_{''.join(secrets.choice(_ID_ALPHABET) for _ in range(length))}"


def new_join_code() -> str:
    """A random 6-character join code such as `K7Q2MX`."""
    return "".join(secrets.choice(_JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))


def normalize_join_code(raw: str) -> str:
    """Upper-case a typed join code and drop spaces and dashes (`k7q-2mx` -> `K7Q2MX`)."""
    return "".join(ch for ch in raw.upper() if ch.isalnum())
