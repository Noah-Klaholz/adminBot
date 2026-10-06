"""Audit log: DB table + private audit room (plan §7). Stores counts and user IDs, never emails."""
from __future__ import annotations

from datetime import datetime, timezone
import logging

from mautrix.types import RoomID, UserID

from .db import AuditEntry, Store
from .matrix_ops import MatrixOps
from .strings import t

log = logging.getLogger("maubot.adminbot.audit")


def format_entry(entry: AuditEntry) -> str:
    parts = [f"`{entry.action}` by {entry.actor}"]
    if entry.course:
        parts.append(f"course {entry.course}")
    if entry.bot:
        parts.append(f"bot {entry.bot}")
    if entry.target_count is not None:
        parts.append(f"{entry.target_count} targets")
    if entry.job_id is not None:
        parts.append(f"job #{entry.job_id}")
    if entry.result != "ok":
        parts.append(f"**{entry.result}**")
    return ", ".join(parts)


class Audit:
    def __init__(self, store: Store, ops: MatrixOps, audit_room: RoomID | None) -> None:
        self.store = store
        self.ops = ops
        self.audit_room = audit_room or None

    async def log(
        self,
        actor: UserID,
        action: str,
        *,
        course: str | None = None,
        bot: str | None = None,
        target_count: int | None = None,
        job_id: int | None = None,
        result: str = "ok",
    ) -> None:
        """Write the DB row, then post a one-line notice to the audit room.
        A failing room post must not fail the action."""
        entry = AuditEntry(
            id=None, ts=datetime.now(timezone.utc), actor=actor, action=action, course=course, bot=bot,
            target_count=target_count, job_id=job_id, result=result,
        )
        entry.id = await self.store.add_audit(entry)
        await self.post(format_entry(entry))

    async def recent(self, n: int = 20) -> list[AuditEntry]:
        return await self.store.recent_audit(n)

    async def report_check(self, check: str, ok: bool, detail: str = "") -> None:
        key = "msg.check_ok" if ok else "msg.check_failed"
        await self.post(t(key, check=check, detail=detail))

    async def post(self, text: str) -> None:
        if not self.audit_room:
            return
        try:
            await self.ops.send(self.audit_room, text)
        except Exception as e:
            log.warning(f"Couldn't post to the audit room: {e}")
