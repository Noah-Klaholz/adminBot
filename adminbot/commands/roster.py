"""!roster sync <code> [--remove]: compare a list with the course members (plan §11)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maubot import MessageEvent

from ..attachments import Upload
from ..authz import Role
from ..db import Course, Job, JobItem
from ..identity import group_by_user
from ..jobs import JobKind
from ..parsing import decode_text, extract_emails, split_args, split_first_line
from ..roster import diff_roster
from ..strings import t
from .base import CommandGroup, command
from .invite import list_preview

if TYPE_CHECKING:
    from ..bot import AdminBot


class RosterCommands(CommandGroup):
    def __init__(self, bot: AdminBot) -> None:
        super().__init__(bot)
        bot.jobs.register(JobKind.ROSTER_REMOVE, self.remove_item, audit_action="roster.remove")

    @command("roster", "sync", usage="usage.roster.sync", role=Role.TA)
    async def sync(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.roster.sync")
        first, rest = split_first_line(args)
        a = split_args(first, {"remove": False, "dry-run": False}, usage)
        course = await self.require_course(evt, a.require(1, usage)[0], Role.TA)
        text = " ".join(a.positionals[1:]) + "\n" + rest
        context = {"code": course.code, "remove": a.flag("remove"), "dry_run": a.flag("dry-run")}
        if extract_emails(text):
            await self.compare(evt, course, text, context)
        else:
            await self.with_file(evt, self.on_file, context)

    async def on_file(self, evt: MessageEvent, upload: Upload, context: dict[str, Any]) -> None:
        course = await self.require_course(evt, context["code"], Role.TA)
        await self.compare(evt, course, decode_text(upload.data), context)

    async def compare(self, evt: MessageEvent, course: Course, text: str, context: dict[str, Any]) -> None:
        users, invalid = group_by_user(
            extract_emails(text), self.bot.server_name, self.bot.config["identity.allowed_domains"])
        members = await self.bot.ops.joined_or_invited(course.space_id)
        protected = set(await self.bot.store.get_staff(course.code)) | {self.bot.client.mxid}
        diff = diff_roster(set(users), members, protected)
        summary = t("msg.roster_summary", code=course.code, listed=len(users), unchanged=len(diff.unchanged),
                    missing=len(diff.to_add), extra=len(diff.to_remove))
        if diff.to_add:
            summary += "\n" + t("msg.roster_missing", users=list_preview(diff.to_add), code=course.code)
        if diff.to_remove:
            summary += "\n" + t("msg.roster_extra", users=list_preview(diff.to_remove))
        if invalid:
            summary += "\n" + t("msg.invalid_entries", entries=list_preview(invalid))
        if context["remove"] and diff.to_remove:
            items = [{"user_id": u, "label": u} for u in diff.to_remove]
            job_context = {"course": course.code, "role": Role.TA.value}
            await self.propose(evt, JobKind.ROSTER_REMOVE.value, summary, items, job_context, context["dry_run"])
        else:
            await self.reply_text(evt, summary)

    async def remove_item(self, job: Job, item: JobItem) -> None:
        """Kick from every child room first, then from the space."""
        course = await self.course(job.context["course"])
        user_id = item.payload["user_id"]
        reason = t("msg.kick_reason_roster")
        for room_id in await self.bot.ops.children(course.space_id):
            await self.bot.ops.kick(room_id, user_id, reason)
        await self.bot.ops.kick(course.space_id, user_id, reason)
