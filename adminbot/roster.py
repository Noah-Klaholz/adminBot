"""Roster diff and group splitting. Pure functions, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field

from mautrix.types import UserID


@dataclass
class RosterDiff:
    to_add: list[UserID] = field(default_factory=list)  # on the list, not in the space
    to_remove: list[UserID] = field(default_factory=list)  # in the space, not on the list
    unchanged: list[UserID] = field(default_factory=list)


def diff_roster(wanted: set[UserID], members: set[UserID], protected: set[UserID]) -> RosterDiff:
    """protected = professors, TAs and the bot: never in to_remove."""
    return RosterDiff(
        to_add=sorted(wanted - members - protected),
        to_remove=sorted(members - wanted - protected),
        unchanged=sorted((wanted & members) - protected),
    )


def round_robin(users: list[UserID], groups: int) -> dict[int, list[UserID]]:
    """Split users into groups 1..n as evenly as possible, in a stable (sorted) order."""
    if groups < 1:
        raise ValueError("groups must be >= 1")
    result: dict[int, list[UserID]] = {n: [] for n in range(1, groups + 1)}
    for i, user in enumerate(sorted(users)):
        result[i % groups + 1].append(user)
    return result
