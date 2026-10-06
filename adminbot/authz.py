"""Roles and the single place where permissions are checked (plan §5).

Hierarchy: admin > professor (course owner) > ta (per course). Ownership comes from the DB,
never from room power levels. An email domain never grants anything.
"""
from __future__ import annotations

from enum import Enum

from mautrix.types import UserID

from .config import Config
from .db import Store
from .errors import Forbidden

OWNER = "owner"
TA = "ta"


class Role(str, Enum):
    ADMIN = "admin"
    PROFESSOR = "professor"  # without a course: on the allowlist; with a course: owns it
    TA = "ta"  # with a course: TA (or owner) of it; without: staff of any course


class Authz:
    def __init__(self, store: Store, config: Config) -> None:
        self.store = store
        self.config = config

    def is_admin(self, user_id: UserID) -> bool:
        return user_id in (self.config["admins"] or [])

    async def roles(self, user_id: UserID) -> dict[str, list[str]]:
        """For !whoami: {'global': [...], 'courses': ['CS101 (owner)', ...]}."""
        result: dict[str, list[str]] = {"global": [], "courses": []}
        if self.is_admin(user_id):
            result["global"].append(Role.ADMIN.value)
        if await self.store.is_professor(user_id):
            result["global"].append(Role.PROFESSOR.value)
        for course in await self.store.list_courses(user_id):
            role = await self.store.staff_role(course.code, user_id)
            result["courses"].append(f"{course.code} ({role})")
        return result

    async def has(self, user_id: UserID, role: Role | None, course: str | None = None) -> bool:
        """True if the user has `role` or a higher one (admin satisfies everything, the course
        owner satisfies ta). role None = any known user."""
        if self.is_admin(user_id):
            return True
        if role is None:
            return await self.is_known(user_id)
        if role == Role.ADMIN:
            return False
        if course is not None:
            staff_role = await self.store.staff_role(course, user_id)
            if role == Role.PROFESSOR:
                return staff_role == OWNER
            return staff_role in (OWNER, TA)
        if role == Role.PROFESSOR:
            return await self.store.is_professor(user_id)
        return await self.store.is_professor(user_id) or await self.store.is_staff_anywhere(user_id)

    async def require(self, user_id: UserID, role: Role | None, course: str | None = None) -> None:
        if not await self.has(user_id, role, course):
            raise Forbidden("err.forbidden")

    async def is_known(self, user_id: UserID) -> bool:
        """Admin, professor or staff of any course. Everyone else is ignored silently."""
        return (
            self.is_admin(user_id)
            or await self.store.is_professor(user_id)
            or await self.store.is_staff_anywhere(user_id)
        )
