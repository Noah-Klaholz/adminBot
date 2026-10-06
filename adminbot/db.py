"""Data access. All SQL lives here; services and commands only call Store methods.

Queries use $n placeholders (mautrix translates them for SQLite) and only SQL that both
SQLite >= 3.35 and Postgres understand (RETURNING, ON CONFLICT).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from typing import Any

from mautrix.types import EventID, RoomID, UserID
from mautrix.util.async_db import Database, Scheme


def now_ms() -> int:
    return int(time.time() * 1000)


def to_dt(ms: int | None) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms is not None else None


def to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@dataclass
class Course:
    code: str
    title: str
    semester: str
    space_id: RoomID
    owner: UserID
    archived: bool
    created_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Course:
        return cls(
            code=row["code"],
            title=row["title"],
            semester=row["semester"],
            space_id=row["space_id"],
            owner=row["owner"],
            archived=bool(row["archived"]),
            created_at=to_dt(row["created_at"]),
        )


@dataclass
class SignupToken:
    token_hash: str
    kind: str  # "signup" | "reset"
    user_id: UserID
    emails: list[str]
    course: str | None
    created_by: UserID
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None

    @classmethod
    def from_row(cls, row: Any) -> SignupToken:
        return cls(
            token_hash=row["token_hash"],
            kind=row["kind"],
            user_id=row["user_id"],
            emails=json.loads(row["emails"]),
            course=row["course"],
            created_by=row["created_by"],
            created_at=to_dt(row["created_at"]),
            expires_at=to_dt(row["expires_at"]),
            used_at=to_dt(row["used_at"]),
        )


@dataclass
class PendingInvite:
    user_id: UserID
    course: str
    mode: str  # "invite" | "force_join"
    created_by: UserID


@dataclass
class PlanRow:
    code: str
    kind: str
    requester: UserID
    room_id: RoomID
    summary: str
    items: list[dict[str, Any]]
    context: dict[str, Any]
    expires_at: datetime


@dataclass
class Job:
    id: int
    kind: str
    requester: UserID
    room_id: RoomID
    context: dict[str, Any]
    progress_event_id: EventID | None
    state: str  # "running" | "done"

    @classmethod
    def from_row(cls, row: Any) -> Job:
        return cls(
            id=row["id"],
            kind=row["kind"],
            requester=row["requester"],
            room_id=row["room_id"],
            context=json.loads(row["context"]),
            progress_event_id=row["progress_event_id"],
            state=row["state"],
        )


@dataclass
class JobItem:
    job_id: int
    idx: int
    payload: dict[str, Any]
    state: str  # "pending" | "done" | "failed"
    error: str | None

    @classmethod
    def from_row(cls, row: Any) -> JobItem:
        return cls(
            job_id=row["job_id"],
            idx=row["idx"],
            payload=json.loads(row["payload"]),
            state=row["state"],
            error=row["error"],
        )


@dataclass
class AuditEntry:
    id: int | None
    ts: datetime
    actor: UserID
    action: str
    course: str | None = None
    bot: str | None = None
    target_count: int | None = None
    job_id: int | None = None
    result: str = "ok"


@dataclass
class UserBot:
    owner: UserID
    name: str
    plugin_id: str
    instance_id: str
    user_id: UserID
    sha256: str
    state: str  # "running" | "stopped" | "deleted"

    @classmethod
    def from_row(cls, row: Any) -> UserBot:
        return cls(**{k: row[k] for k in ("owner", "name", "plugin_id", "instance_id", "user_id", "sha256", "state")})


@dataclass
class BotRequest:
    id: int | None
    owner: UserID
    name: str
    action: str  # "create" | "update"
    plugin_id: str
    version: str
    sha256: str
    mbp: bytes
    state: str  # "pending" | "approved" | "rejected" | "done" | "failed"
    decided_by: UserID | None = None
    reason: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> BotRequest:
        return cls(
            id=row["id"],
            owner=row["owner"],
            name=row["name"],
            action=row["action"],
            plugin_id=row["plugin_id"],
            version=row["version"],
            sha256=row["sha256"],
            mbp=bytes(row["mbp"]),
            state=row["state"],
            decided_by=row["decided_by"],
            reason=row["reason"],
        )


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def is_postgres(self) -> bool:
        """True if the plugin DB is Postgres (self-check: otherwise not in the backup)."""
        return self.db.scheme in (Scheme.POSTGRES, Scheme.COCKROACH)

    # --- professors ---------------------------------------------------------------
    async def add_professor(self, user_id: UserID, added_by: UserID) -> bool:
        q = (
            "INSERT INTO professor (user_id, added_by, added_at) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id) DO NOTHING RETURNING user_id"
        )
        return await self.db.fetchval(q, user_id, added_by, now_ms()) is not None

    async def remove_professor(self, user_id: UserID) -> bool:
        q = "DELETE FROM professor WHERE user_id=$1 RETURNING user_id"
        return await self.db.fetchval(q, user_id) is not None

    async def list_professors(self) -> list[UserID]:
        rows = await self.db.fetch("SELECT user_id FROM professor ORDER BY user_id")
        return [row["user_id"] for row in rows]

    async def is_professor(self, user_id: UserID) -> bool:
        q = "SELECT 1 FROM professor WHERE user_id=$1"
        return await self.db.fetchval(q, user_id) is not None

    # --- courses and staff ----------------------------------------------------------
    async def create_course(self, course: Course) -> None:
        q = (
            "INSERT INTO course (code, title, semester, space_id, owner, archived, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)"
        )
        await self.db.execute(
            q, course.code, course.title, course.semester, course.space_id, course.owner,
            course.archived, to_ms(course.created_at),
        )

    async def get_course(self, code: str) -> Course | None:
        row = await self.db.fetchrow("SELECT * FROM course WHERE code=$1", code)
        return Course.from_row(row) if row else None

    async def get_course_by_space(self, space_id: RoomID) -> Course | None:
        row = await self.db.fetchrow("SELECT * FROM course WHERE space_id=$1", space_id)
        return Course.from_row(row) if row else None

    async def list_courses(self, user_id: UserID | None = None) -> list[Course]:
        """All courses, or only those where user_id is staff."""
        if user_id is None:
            rows = await self.db.fetch("SELECT * FROM course ORDER BY code")
        else:
            q = (
                "SELECT course.* FROM course JOIN course_staff ON course.code=course_staff.code "
                "WHERE course_staff.user_id=$1 ORDER BY course.code"
            )
            rows = await self.db.fetch(q, user_id)
        return [Course.from_row(row) for row in rows]

    async def set_course_archived(self, code: str, archived: bool) -> None:
        await self.db.execute("UPDATE course SET archived=$2 WHERE code=$1", code, archived)

    async def rename_course(self, old: str, new: str) -> None:
        """Rollover: the old course gets a suffixed code, everything referring to it follows."""
        async with self.db.acquire() as conn, conn.transaction():
            await conn.execute("UPDATE course SET code=$2 WHERE code=$1", old, new)
            await conn.execute("UPDATE course_staff SET code=$2 WHERE code=$1", old, new)
            await conn.execute("UPDATE pending_invite SET course=$2 WHERE course=$1", old, new)
            await conn.execute("UPDATE signup_token SET course=$2 WHERE course=$1", old, new)

    async def add_staff(self, code: str, user_id: UserID, role: str) -> None:
        q = (
            "INSERT INTO course_staff (code, user_id, role) VALUES ($1, $2, $3) "
            "ON CONFLICT (code, user_id) DO UPDATE SET role=excluded.role"
        )
        await self.db.execute(q, code, user_id, role)

    async def remove_staff(self, code: str, user_id: UserID) -> bool:
        q = "DELETE FROM course_staff WHERE code=$1 AND user_id=$2 RETURNING user_id"
        return await self.db.fetchval(q, code, user_id) is not None

    async def get_staff(self, code: str) -> dict[UserID, str]:
        rows = await self.db.fetch(
            "SELECT user_id, role FROM course_staff WHERE code=$1 ORDER BY role, user_id", code
        )
        return {row["user_id"]: row["role"] for row in rows}

    async def staff_role(self, code: str, user_id: UserID) -> str | None:
        q = "SELECT role FROM course_staff WHERE code=$1 AND user_id=$2"
        return await self.db.fetchval(q, code, user_id)

    async def is_staff_anywhere(self, user_id: UserID) -> bool:
        return await self.db.fetchval("SELECT 1 FROM course_staff WHERE user_id=$1 LIMIT 1", user_id) is not None

    # --- templates --------------------------------------------------------------------
    async def save_template(self, name: str, scope: str, owner: UserID | None, yaml: str) -> None:
        q = (
            "INSERT INTO template (name, scope, owner, yaml, created_at) VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (name, scope, owner) DO UPDATE SET yaml=excluded.yaml, created_at=excluded.created_at"
        )
        await self.db.execute(q, name, scope, owner or "", yaml, now_ms())

    async def get_template(self, name: str, user_id: UserID) -> str | None:
        """Personal template of user_id first, then global."""
        q = (
            "SELECT yaml FROM template WHERE name=$1 AND "
            "((scope='personal' AND owner=$2) OR scope='global') "
            "ORDER BY CASE WHEN scope='personal' THEN 0 ELSE 1 END LIMIT 1"
        )
        return await self.db.fetchval(q, name, user_id)

    async def list_templates(self, user_id: UserID) -> list[tuple[str, str]]:
        q = (
            "SELECT name, scope FROM template WHERE (scope='personal' AND owner=$1) OR scope='global' "
            "ORDER BY scope DESC, name"
        )
        return [(row["name"], row["scope"]) for row in await self.db.fetch(q, user_id)]

    async def delete_template(self, name: str, scope: str, owner: UserID | None) -> bool:
        q = "DELETE FROM template WHERE name=$1 AND scope=$2 AND owner=$3 RETURNING name"
        return await self.db.fetchval(q, name, scope, owner or "") is not None

    # --- signup / reset tokens and pending invites ---------------------------------------
    async def add_token(self, token: SignupToken) -> None:
        q = (
            "INSERT INTO signup_token (token_hash, kind, user_id, emails, course, created_by, "
            "created_at, expires_at, used_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NULL)"
        )
        await self.db.execute(
            q, token.token_hash, token.kind, token.user_id, json.dumps(token.emails), token.course,
            token.created_by, to_ms(token.created_at), to_ms(token.expires_at),
        )

    async def get_token(self, token_hash: str) -> SignupToken | None:
        row = await self.db.fetchrow("SELECT * FROM signup_token WHERE token_hash=$1", token_hash)
        return SignupToken.from_row(row) if row else None

    async def tokens_for(self, user_id: UserID, kind: str) -> list[SignupToken]:
        """Open (unused) tokens of a user."""
        q = (
            "SELECT * FROM signup_token WHERE user_id=$1 AND kind=$2 AND used_at IS NULL "
            "ORDER BY created_at"
        )
        return [SignupToken.from_row(row) for row in await self.db.fetch(q, user_id, kind)]

    async def burn_token(self, token_hash: str) -> bool:
        """Atomic single use: only one caller ever gets True for a token."""
        now = now_ms()
        q = (
            "UPDATE signup_token SET used_at=$2 "
            "WHERE token_hash=$1 AND used_at IS NULL AND expires_at>$2 RETURNING token_hash"
        )
        return await self.db.fetchval(q, token_hash, now) is not None

    async def unburn_token(self, token_hash: str) -> None:
        """Undo burn_token when creating the account failed, so the link still works."""
        await self.db.execute("UPDATE signup_token SET used_at=NULL WHERE token_hash=$1", token_hash)

    async def delete_tokens_for(self, user_id: UserID, kind: str) -> int:
        q = "DELETE FROM signup_token WHERE user_id=$1 AND kind=$2 RETURNING token_hash"
        return len(await self.db.fetch(q, user_id, kind))

    async def list_open_tokens(self, kind: str = "signup", course: str | None = None) -> list[SignupToken]:
        if course is None:
            q = "SELECT * FROM signup_token WHERE kind=$1 AND used_at IS NULL ORDER BY course, user_id"
            rows = await self.db.fetch(q, kind)
        else:
            q = (
                "SELECT * FROM signup_token WHERE kind=$1 AND used_at IS NULL AND course=$2 "
                "ORDER BY user_id"
            )
            rows = await self.db.fetch(q, kind, course)
        return [SignupToken.from_row(row) for row in rows]

    async def expire_tokens(self) -> int:
        """Delete expired and used tokens, which also deletes their emails (data protection, §7),
        and pending invites whose user has no open signup link left."""
        q = "DELETE FROM signup_token WHERE expires_at<=$1 OR used_at IS NOT NULL RETURNING token_hash"
        deleted = len(await self.db.fetch(q, now_ms()))
        await self.db.execute(
            "DELETE FROM pending_invite WHERE user_id NOT IN "
            "(SELECT user_id FROM signup_token WHERE kind='signup' AND used_at IS NULL)"
        )
        return deleted

    async def add_pending_invite(self, invite: PendingInvite) -> None:
        q = (
            "INSERT INTO pending_invite (user_id, course, mode, created_by) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (user_id, course) DO UPDATE SET mode=excluded.mode"
        )
        await self.db.execute(q, invite.user_id, invite.course, invite.mode, invite.created_by)

    async def pending_invites(self, user_id: UserID) -> list[PendingInvite]:
        rows = await self.db.fetch("SELECT * FROM pending_invite WHERE user_id=$1", user_id)
        return [PendingInvite(row["user_id"], row["course"], row["mode"], row["created_by"]) for row in rows]

    async def delete_pending_invites(self, user_id: UserID) -> None:
        await self.db.execute("DELETE FROM pending_invite WHERE user_id=$1", user_id)

    # --- confirm plans ------------------------------------------------------------------
    async def save_plan(self, plan: PlanRow) -> None:
        q = (
            "INSERT INTO plan (code, kind, requester, room_id, summary, items, context, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
        )
        await self.db.execute(
            q, plan.code, plan.kind, plan.requester, plan.room_id, plan.summary,
            json.dumps(plan.items), json.dumps(plan.context), to_ms(plan.expires_at),
        )

    async def pop_plan(self, code: str) -> PlanRow | None:
        row = await self.db.fetchrow("DELETE FROM plan WHERE code=$1 RETURNING *", code)
        if not row:
            return None
        return PlanRow(
            code=row["code"], kind=row["kind"], requester=row["requester"], room_id=row["room_id"],
            summary=row["summary"], items=json.loads(row["items"]),
            context=json.loads(row["context"]), expires_at=to_dt(row["expires_at"]),
        )

    async def get_plan_requester(self, code: str) -> UserID | None:
        return await self.db.fetchval("SELECT requester FROM plan WHERE code=$1", code)

    async def delete_plans_of(self, requester: UserID) -> int:
        q = "DELETE FROM plan WHERE requester=$1 RETURNING code"
        return len(await self.db.fetch(q, requester))

    async def expire_plans(self) -> int:
        return len(await self.db.fetch("DELETE FROM plan WHERE expires_at<=$1 RETURNING code", now_ms()))

    # --- jobs ---------------------------------------------------------------------------
    async def create_job(self, kind: str, requester: UserID, room_id: RoomID, context: dict[str, Any],
                         items: list[dict[str, Any]]) -> int:
        async with self.db.acquire() as conn, conn.transaction():
            job_id = await conn.fetchval(
                "INSERT INTO job (kind, requester, room_id, context, state, created_at) "
                "VALUES ($1, $2, $3, $4, 'running', $5) RETURNING id",
                kind, requester, room_id, json.dumps(context), now_ms(),
            )
            for idx, payload in enumerate(items):
                await conn.execute(
                    "INSERT INTO job_item (job_id, idx, payload, state) VALUES ($1, $2, $3, 'pending')",
                    job_id, idx, json.dumps(payload),
                )
        return job_id

    async def get_job(self, job_id: int) -> Job | None:
        row = await self.db.fetchrow("SELECT * FROM job WHERE id=$1", job_id)
        return Job.from_row(row) if row else None

    async def set_job_progress_event(self, job_id: int, event_id: EventID) -> None:
        await self.db.execute("UPDATE job SET progress_event_id=$2 WHERE id=$1", job_id, event_id)

    async def set_job_context(self, job_id: int, context: dict[str, Any]) -> None:
        """Used to drop emails from a job's context once they are no longer needed (§7)."""
        await self.db.execute("UPDATE job SET context=$2 WHERE id=$1", job_id, json.dumps(context))

    async def scrub_job_items(self, job_id: int) -> None:
        """Drop payloads (e.g. per-student messages) of finished items; keeps the labels."""
        rows = await self.db.fetch("SELECT idx, payload FROM job_item WHERE job_id=$1", job_id)
        for row in rows:
            label = json.loads(row["payload"]).get("label")
            await self.db.execute("UPDATE job_item SET payload=$3 WHERE job_id=$1 AND idx=$2",
                                  job_id, row["idx"], json.dumps({"label": label} if label else {}))

    async def finish_job(self, job_id: int) -> None:
        await self.db.execute("UPDATE job SET state='done', finished_at=$2 WHERE id=$1", job_id, now_ms())

    async def unfinished_jobs(self) -> list[Job]:
        rows = await self.db.fetch("SELECT * FROM job WHERE state='running' ORDER BY id")
        return [Job.from_row(row) for row in rows]

    async def pending_items(self, job_id: int) -> list[JobItem]:
        q = "SELECT * FROM job_item WHERE job_id=$1 AND state='pending' ORDER BY idx"
        return [JobItem.from_row(row) for row in await self.db.fetch(q, job_id)]

    async def set_item_state(self, job_id: int, idx: int, state: str, error: str | None = None) -> None:
        q = "UPDATE job_item SET state=$3, error=$4 WHERE job_id=$1 AND idx=$2"
        await self.db.execute(q, job_id, idx, state, error)

    async def item_counts(self, job_id: int) -> dict[str, int]:
        q = "SELECT state, COUNT(*) AS n FROM job_item WHERE job_id=$1 GROUP BY state"
        return {row["state"]: row["n"] for row in await self.db.fetch(q, job_id)}

    async def failed_items(self, job_id: int, limit: int = 50) -> list[JobItem]:
        q = "SELECT * FROM job_item WHERE job_id=$1 AND state='failed' ORDER BY idx LIMIT $2"
        return [JobItem.from_row(row) for row in await self.db.fetch(q, job_id, limit)]

    # --- audit --------------------------------------------------------------------------
    async def add_audit(self, entry: AuditEntry) -> int:
        q = (
            "INSERT INTO audit (ts, actor, action, course, bot, target_count, job_id, result) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id"
        )
        return await self.db.fetchval(
            q, to_ms(entry.ts), entry.actor, entry.action, entry.course, entry.bot,
            entry.target_count, entry.job_id, entry.result,
        )

    async def recent_audit(self, n: int) -> list[AuditEntry]:
        rows = await self.db.fetch("SELECT * FROM audit ORDER BY id DESC LIMIT $1", n)
        return [
            AuditEntry(
                id=row["id"], ts=to_dt(row["ts"]), actor=row["actor"], action=row["action"],
                course=row["course"], bot=row["bot"], target_count=row["target_count"],
                job_id=row["job_id"], result=row["result"],
            )
            for row in rows
        ]

    # --- professor bots -------------------------------------------------------------------
    async def plugin_owner(self, plugin_id: str) -> UserID | None:
        return await self.db.fetchval("SELECT owner FROM plugin_owner WHERE plugin_id=$1", plugin_id)

    async def set_plugin_owner(self, plugin_id: str, owner: UserID) -> None:
        q = (
            "INSERT INTO plugin_owner (plugin_id, owner) VALUES ($1, $2) "
            "ON CONFLICT (plugin_id) DO UPDATE SET owner=excluded.owner"
        )
        await self.db.execute(q, plugin_id, owner)

    async def delete_plugin_owner(self, plugin_id: str) -> None:
        await self.db.execute("DELETE FROM plugin_owner WHERE plugin_id=$1", plugin_id)

    async def add_bot_request(self, request: BotRequest) -> int:
        q = (
            "INSERT INTO bot_request (owner, name, action, plugin_id, version, sha256, mbp, state, "
            "created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id"
        )
        return await self.db.fetchval(
            q, request.owner, request.name, request.action, request.plugin_id, request.version,
            request.sha256, request.mbp, request.state, now_ms(),
        )

    async def get_bot_request(self, request_id: int) -> BotRequest | None:
        row = await self.db.fetchrow("SELECT * FROM bot_request WHERE id=$1", request_id)
        return BotRequest.from_row(row) if row else None

    async def pending_bot_requests(self) -> list[BotRequest]:
        rows = await self.db.fetch("SELECT * FROM bot_request WHERE state='pending' ORDER BY id")
        return [BotRequest.from_row(row) for row in rows]

    async def set_bot_request_state(self, request_id: int, state: str, decided_by: UserID | None = None,
                                    reason: str | None = None, drop_package: bool = False) -> None:
        q = (
            "UPDATE bot_request SET state=$2, decided_by=COALESCE($3, decided_by), "
            "reason=COALESCE($4, reason) WHERE id=$1"
        )
        await self.db.execute(q, request_id, state, decided_by, reason)
        if drop_package:
            await self.db.execute("UPDATE bot_request SET mbp=$2 WHERE id=$1", request_id, b"")

    async def save_user_bot(self, bot: UserBot) -> None:
        q = (
            "INSERT INTO user_bot (owner, name, plugin_id, instance_id, user_id, sha256, state) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (owner, name) DO UPDATE SET "
            "plugin_id=excluded.plugin_id, instance_id=excluded.instance_id, user_id=excluded.user_id, "
            "sha256=excluded.sha256, state=excluded.state"
        )
        await self.db.execute(q, bot.owner, bot.name, bot.plugin_id, bot.instance_id, bot.user_id,
                              bot.sha256, bot.state)

    async def get_user_bot(self, owner: UserID, name: str) -> UserBot | None:
        q = "SELECT * FROM user_bot WHERE owner=$1 AND name=$2 AND state<>'deleted'"
        row = await self.db.fetchrow(q, owner, name)
        return UserBot.from_row(row) if row else None

    async def list_user_bots(self, owner: UserID | None = None) -> list[UserBot]:
        if owner is None:
            rows = await self.db.fetch("SELECT * FROM user_bot WHERE state<>'deleted' ORDER BY owner, name")
        else:
            q = "SELECT * FROM user_bot WHERE owner=$1 AND state<>'deleted' ORDER BY name"
            rows = await self.db.fetch(q, owner)
        return [UserBot.from_row(row) for row in rows]

    async def count_user_bots(self, owner: UserID) -> int:
        q = "SELECT COUNT(*) FROM user_bot WHERE owner=$1 AND state<>'deleted'"
        return await self.db.fetchval(q, owner)

    async def set_user_bot_state(self, owner: UserID, name: str, state: str) -> None:
        await self.db.execute("UPDATE user_bot SET state=$3 WHERE owner=$1 AND name=$2", owner, name, state)

    async def instances_using_plugin(self, plugin_id: str) -> int:
        q = "SELECT COUNT(*) FROM user_bot WHERE plugin_id=$1 AND state<>'deleted'"
        return await self.db.fetchval(q, plugin_id)
