"""Job runner: confirmed plans run item by item, throttled, resumable (plan §6).

Each item is processed by the handler registered for the job kind. State is kept per item in
the DB, so a restart resumes where it stopped. One progress message per job is edited in place.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import logging
import time
from typing import TYPE_CHECKING, Awaitable, Callable

from mautrix.errors import MatrixRequestError

from .confirm import Plan
from .db import Job, JobItem
from .errors import AdminBotError
from .strings import t

if TYPE_CHECKING:
    from .bot import AdminBot

log = logging.getLogger("maubot.adminbot.jobs")


class JobKind(str, Enum):
    INVITE = "invite"
    ROSTER_REMOVE = "roster_remove"
    GROUPS_CREATE = "groups_create"
    GROUPS_ASSIGN = "groups_assign"
    GROUPS_POST = "groups_post"
    COURSE_ARCHIVE = "course_archive"
    BOT_PROVISION = "bot_provision"
    BOT_DELETE = "bot_delete"


# Processes one item; raise to mark it failed (the message becomes the item's error).
ItemHandler = Callable[[Job, JobItem], Awaitable[None]]
# Runs once: on_start right after confirm (before any item), on_finish after the last item.
JobHook = Callable[[Job], Awaitable[None]]


@dataclass
class JobSpec:
    item: ItemHandler
    on_start: JobHook | None = None
    on_finish: JobHook | None = None
    audit_action: str = ""


def describe_error(e: Exception) -> str:
    if isinstance(e, AdminBotError):
        return str(e)
    if isinstance(e, MatrixRequestError):
        return getattr(e, "message", None) or str(e)
    return f"{type(e).__name__}: {e}"


class JobRunner:
    PROGRESS_EVERY_SECONDS = 5

    def __init__(self, bot: AdminBot, delay_seconds: float) -> None:
        self.bot = bot
        self.delay_seconds = delay_seconds
        self.specs: dict[str, JobSpec] = {}
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.worker: asyncio.Task | None = None

    def register(self, kind: JobKind, item: ItemHandler, *, on_start: JobHook | None = None,
                 on_finish: JobHook | None = None, audit_action: str = "") -> None:
        self.specs[kind.value] = JobSpec(item, on_start, on_finish, audit_action or kind.value)

    async def start(self) -> None:
        """Start the worker task and enqueue unfinished jobs from the DB."""
        self.worker = asyncio.create_task(self._worker())
        for job in await self.bot.store.unfinished_jobs():
            log.info(f"Resuming job #{job.id} ({job.kind})")
            self.queue.put_nowait(job.id)

    async def stop(self) -> None:
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
            self.worker = None

    async def submit(self, plan: Plan) -> int:
        """Create the job + items from a confirmed plan, run on_start, post the progress
        message, enqueue."""
        job_id = await self.bot.store.create_job(plan.kind, plan.requester, plan.room_id, plan.context, plan.items)
        job = await self.bot.store.get_job(job_id)
        spec = self.specs[plan.kind]
        if spec.on_start:
            try:
                await spec.on_start(job)
            except Exception as e:
                # The items still run; the requester learns what the first step missed.
                log.exception(f"on_start of job #{job_id} failed")
                await self.bot.ops.send(plan.room_id, t("msg.job_start_failed", id=job_id, error=describe_error(e)))
        if plan.items:
            event_id = await self.bot.ops.send(
                plan.room_id, t("msg.job_started", id=job_id, total=len(plan.items)))
            await self.bot.store.set_job_progress_event(job_id, event_id)
        self.queue.put_nowait(job_id)
        return job_id

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                job = await self.bot.store.get_job(job_id)
                if job and job.state == "running":
                    await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(f"Job #{job_id} crashed")
                try:
                    await self.bot.store.finish_job(job_id)
                except Exception:
                    log.exception(f"Couldn't mark job #{job_id} as finished")

    async def _run(self, job: Job) -> None:
        spec = self.specs.get(job.kind)
        if spec is None:
            log.error(f"No handler for job kind {job.kind}, job #{job.id} stays unfinished")
            return
        last_progress = time.monotonic()
        for item in await self.bot.store.pending_items(job.id):
            try:
                await spec.item(job, item)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not isinstance(e, (AdminBotError, MatrixRequestError)):
                    log.exception(f"Job #{job.id} item {item.idx} failed")
                await self.bot.store.set_item_state(job.id, item.idx, "failed", describe_error(e))
            else:
                await self.bot.store.set_item_state(job.id, item.idx, "done")
            if time.monotonic() - last_progress > self.PROGRESS_EVERY_SECONDS:
                await self._update_progress(job)
                last_progress = time.monotonic()
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
        await self._finish(job, spec)

    async def _update_progress(self, job: Job) -> None:
        if not job.progress_event_id:
            return
        counts = await self.bot.store.item_counts(job.id)
        total = sum(counts.values())
        text = t("msg.job_progress", id=job.id, done=counts.get("done", 0) + counts.get("failed", 0),
                 total=total, failed=counts.get("failed", 0))
        try:
            await self.bot.ops.send(job.room_id, text, edits=job.progress_event_id)
        except Exception as e:
            log.warning(f"Couldn't update progress of job #{job.id}: {e}")

    async def _finish(self, job: Job, spec: JobSpec) -> None:
        counts = await self.bot.store.item_counts(job.id)
        total = sum(counts.values())
        failed = counts.get("failed", 0)
        if spec.on_finish:
            try:
                await spec.on_finish(job)
            except Exception as e:
                log.exception(f"on_finish of job #{job.id} failed")
                failed += 1
                await self.bot.ops.send(job.room_id, t("msg.job_finish_failed", id=job.id, error=describe_error(e)))
        await self.bot.store.finish_job(job.id)
        if total:
            await self._update_progress(job)
            lines = [t("msg.job_done", id=job.id, done=counts.get("done", 0), total=total, failed=failed)]
            for item in await self.bot.store.failed_items(job.id):
                target = item.payload.get("label") or item.payload.get("user_id") or item.payload.get("room_id") or item.idx
                lines.append(f"- {target}: {item.error}")
            await self.bot.ops.send(job.room_id, "\n".join(lines))
        await self.bot.audit.log(
            job.requester, spec.audit_action, course=job.context.get("course"), bot=job.context.get("bot"),
            target_count=total, job_id=job.id, result="ok" if not failed else f"{failed} failed",
        )
