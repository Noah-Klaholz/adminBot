"""Email -> Matrix user ID (plan §4). Pure functions, no I/O.

Rules:
- localpart = part before '@', lowercased; it must consist of [a-z0-9._=-] only. Addresses with
  other characters are rejected instead of silently stripped, so two different addresses can
  never collapse into the same account ('max+1' vs 'max1').
- only allowed domains; the same localpart on any allowed domain is ONE account
- reserved localparts and prefixes are never handed out to people
"""
from __future__ import annotations

import re
from typing import Iterable

from mautrix.types import UserID

LOCALPART_VALID = re.compile(r"^[a-z0-9._=-]+$")
EMAIL_SHAPE = re.compile(r"^[^@\s<>\"'(),;:]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
RESERVED_LOCALPARTS = frozenset({"adminbot", "admin"})
RESERVED_PREFIXES = ("bot.", "guest.")
GUEST_PREFIX = "guest."


class IdentityError(ValueError):
    """Invalid email, domain not allowed, or reserved localpart. args[0] is a strings key."""

    def __init__(self, key: str, value: str) -> None:
        super().__init__(key, value)
        self.key = key
        self.value = value


def normalize_email(raw: str) -> str:
    """Strip whitespace, quotes, <...> and mailto:, lowercase. Raises IdentityError if not an address."""
    email = raw.strip().rstrip(".,;").strip().strip("\"'").strip()
    if email.startswith("<") and email.endswith(">"):
        email = email[1:-1].strip()
    if email.lower().startswith("mailto:"):
        email = email[7:]
    email = email.strip().lower()
    if not EMAIL_SHAPE.match(email):
        raise IdentityError("err.email_invalid", raw)
    return email


def _domain_allowed(domain: str, allowed_domains: Iterable[str]) -> bool:
    return domain in {d.strip().lower() for d in allowed_domains}


def is_reserved(localpart: str) -> bool:
    return localpart in RESERVED_LOCALPARTS or localpart.startswith(RESERVED_PREFIXES)


def localpart_for_email(email: str, allowed_domains: Iterable[str]) -> str:
    """Localpart for an allowed address. Raises IdentityError (bad domain, bad characters, reserved)."""
    email = normalize_email(email)
    local, domain = email.rsplit("@", 1)
    if not _domain_allowed(domain, allowed_domains):
        raise IdentityError("err.email_domain", email)
    if not LOCALPART_VALID.match(local):
        raise IdentityError("err.email_chars", email)
    if is_reserved(local):
        raise IdentityError("err.email_reserved", email)
    return local


def user_id_for_email(email: str, server_name: str, allowed_domains: Iterable[str]) -> UserID:
    """'Max.Muster@stud.unibas.ch' -> '@max.muster:<server_name>'."""
    return UserID(f"@{localpart_for_email(email, allowed_domains)}:{server_name}")


def guest_user_id(name: str, server_name: str) -> UserID:
    """Guest accounts (no unibas address): '@guest.<name>:<server_name>'.
    'Jane Doe' -> 'guest.jane.doe'. Characters outside [a-z0-9._=-] are rejected."""
    local = re.sub(r"\s+", ".", name.strip().lower())
    if not local or not LOCALPART_VALID.match(local):
        raise IdentityError("err.guest_name", name)
    return UserID(f"@{GUEST_PREFIX}{local}:{server_name}")


def is_local_user(user_id: str, server_name: str) -> bool:
    return (
        user_id.startswith("@")
        and user_id.count(":") >= 1
        and user_id.split(":", 1)[1] == server_name
        and len(user_id.split(":", 1)[0]) > 1
    )


def group_by_user(
    emails: Iterable[str], server_name: str, allowed_domains: Iterable[str]
) -> tuple[dict[UserID, list[str]], list[str]]:
    """Normalise, de-duplicate and merge both domains.
    Returns ({user_id: [emails]}, [invalid inputs]) in input order."""
    users: dict[UserID, list[str]] = {}
    invalid: list[str] = []
    for raw in emails:
        try:
            email = normalize_email(raw)
            user_id = user_id_for_email(email, server_name, allowed_domains)
        except IdentityError:
            if raw.strip() and raw.strip() not in invalid:
                invalid.append(raw.strip())
            continue
        known = users.setdefault(user_id, [])
        if email not in known:
            known.append(email)
    return users, invalid
