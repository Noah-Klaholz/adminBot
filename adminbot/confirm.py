"""Confirm / dry-run for bulk and destructive commands (plan §6).

Flow: a command builds a plan -> propose() stores it and the command replies with the summary
and a code -> `!confirm <code>` by the same user -> the plan becomes a job (jobs.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets
from typing import Any

from mautrix.types import RoomID, UserID

from .db import PlanRow, Store
from .errors import ConfirmError

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


@dataclass
class Plan:
    code: str
    kind: str  # a jobs.JobKind value
    requester: UserID
    room_id: RoomID
    summary: str  # rendered text shown to the user
    items: list[dict[str, Any]]  # become the job items
    context: dict[str, Any]  # job-wide data (course code, flags, the role to re-check ...)
    expires_at: datetime


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))


class ConfirmRegistry:
    def __init__(self, store: Store, ttl_minutes: int) -> None:
        self.store = store
        self.ttl_minutes = ttl_minutes

    async def propose(self, requester: UserID, room_id: RoomID, kind: str, summary: str,
                      items: list[dict[str, Any]], context: dict[str, Any]) -> Plan:
        """Store the plan and return it. A user has at most one open plan: older ones are dropped."""
        await self.store.delete_plans_of(requester)
        plan = Plan(
            code=new_code(), kind=kind, requester=requester, room_id=room_id, summary=summary,
            items=items, context=context,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=self.ttl_minutes),
        )
        await self.store.save_plan(PlanRow(
            code=plan.code, kind=kind, requester=requester, room_id=room_id, summary=summary,
            items=items, context=context, expires_at=plan.expires_at,
        ))
        return plan

    async def confirm(self, code: str, requester: UserID) -> Plan:
        """Pop and return the plan. ConfirmError if unknown, expired or not the requester's."""
        code = code.strip().lower()
        owner = await self.store.get_plan_requester(code)
        if owner is None or owner != requester:
            raise ConfirmError("err.confirm_unknown", code=code)
        row = await self.store.pop_plan(code)
        if row is None or row.expires_at <= datetime.now(timezone.utc):
            raise ConfirmError("err.confirm_unknown", code=code)
        return Plan(
            code=row.code, kind=row.kind, requester=row.requester, room_id=row.room_id,
            summary=row.summary, items=row.items, context=row.context, expires_at=row.expires_at,
        )

    async def cancel(self, requester: UserID) -> int:
        return await self.store.delete_plans_of(requester)

    async def expire(self) -> int:
        return await self.store.expire_plans()
