"""!invite <code>: bulk invite by email (plan §11).

Existing accounts are invited by a job (one item per user). New people get signup links, created
at confirm time and delivered right away as a mail-merge CSV; the emails are then dropped from the
job's context, so they are only kept with the (expiring) signup tokens.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maubot import MessageEvent

from ..attachments import Upload
from ..authz import Role
from ..db import Course, Job, JobItem
from ..delivery import CsvDelivery
from ..errors import ValidationError
from ..identity import group_by_user
from ..jobs import JobKind
from ..parsing import decode_text, extract_emails, split_args, split_first_line
from ..strings import t
from .base import CommandGroup, command, send_file_to

if TYPE_CHECKING:
    from ..bot import AdminBot

MAX_LISTED = 20


def list_preview(values: list[str], limit: int = MAX_LISTED) -> str:
    shown = ", ".join(values[:limit])
    return shown + (t("msg.and_more", n=len(values) - limit) if len(values) > limit else "")


class InviteCommands(CommandGroup):
    def __init__(self, bot: AdminBot) -> None:
        super().__init__(bot)
        bot.jobs.register(JobKind.INVITE, self.invite_item, on_start=self.invite_start, audit_action="invite")

    @command("invite", usage="usage.invite", role=Role.TA)
    async def invite(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.invite")
        first, rest = split_first_line(args)
        a = split_args(first, {"dry-run": False}, usage)
        course = await self.require_course(evt, a.require(1, usage)[0], Role.TA)
        text = " ".join(a.positionals[1:]) + "\n" + rest
        context = {"code": course.code, "dry_run": a.flag("dry-run")}
        if extract_emails(text):
            await self.plan_invites(evt, course, text, context["dry_run"])
        else:
            await self.with_file(evt, self.on_file, context)

    async def on_file(self, evt: MessageEvent, upload: Upload, context: dict[str, Any]) -> None:
        course = await self.require_course(evt, context["code"], Role.TA)
        await self.plan_invites(evt, course, decode_text(upload.data), context["dry_run"])

    async def plan_invites(self, evt: MessageEvent, course: Course, text: str, dry_run: bool) -> None:
        users, invalid = group_by_user(
            extract_emails(text), self.bot.server_name, self.bot.config["identity.allowed_domains"])
        if not users:
            raise ValidationError("err.no_valid_emails", invalid=list_preview(invalid) or "-")
        existing, new = [], []
        for user_id, emails in users.items():
            if await self.bot.synapse.user_exists(user_id):
                existing.append(user_id)
            else:
                new.append({"user_id": user_id, "emails": emails})
        summary = t("msg.invite_plan", code=course.code, existing=len(existing), new=len(new),
                    invalid=len(invalid))
        if invalid:
            summary += "\n" + t("msg.invalid_entries", entries=list_preview(invalid))
        items = [{"user_id": u, "label": u} for u in existing]
        context = {"course": course.code, "role": Role.TA.value, "new": new}
        await self.propose(evt, JobKind.INVITE.value, summary, items, context, dry_run)

    async def invite_start(self, job: Job) -> None:
        """Signup links for new people, delivered as CSV into the requester's DM."""
        course_code = job.context["course"]
        course = await self.bot.store.get_course(course_code)
        links = []
        try:
            for entry in job.context.get("new", []):
                try:
                    links.append(await self.bot.signups.create(entry["user_id"], entry["emails"], job.requester,
                                                               course_code))
                except ValidationError:
                    # The account appeared since the plan was made: just invite it.
                    if course:
                        await self.bot.ops.invite(course.space_id, entry["user_id"])
            if links:
                await self.bot.audit.log(job.requester, "signup.create", course=course_code,
                                         target_count=len(links), job_id=job.id)
                result = CsvDelivery(self.bot.config["signup.login_url"]).deliver(links)
                await self.bot.ops.send(job.room_id, result.text)
                for filename, data, mimetype in result.files:
                    await send_file_to(self.bot, job.room_id, filename, data, mimetype)
        finally:
            await self.bot.store.set_job_context(job.id, {
                "course": course_code, "role": job.context.get("role"), "new_count": len(links),
            })

    async def invite_item(self, job: Job, item: JobItem) -> None:
        course = await self.course(job.context["course"])
        user_id = item.payload["user_id"]
        if self.bot.config["invites.mode_existing"] == "force_join":
            await self.bot.synapse.force_join(user_id, course.space_id)
        else:
            await self.bot.ops.invite(course.space_id, user_id)
