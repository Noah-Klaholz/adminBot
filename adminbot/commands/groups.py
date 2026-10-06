"""!groups: exercise group rooms, assignment, per-group posts (plan §11)."""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from maubot import MessageEvent
from mautrix.types import RoomID

from .. import templates
from ..attachments import Upload
from ..authz import TA, Role
from ..db import Course, Job, JobItem
from ..errors import ValidationError
from ..jobs import JobKind
from ..parsing import ArgError, decode_text, parse_group_assignments, parse_group_posts, split_args
from ..roster import round_robin
from ..strings import t
from .base import CommandGroup, command
from .invite import list_preview

if TYPE_CHECKING:
    from ..bot import AdminBot

MAX_NEW_GROUPS = 50
PREVIEW_POSTS = 3


class GroupCommands(CommandGroup):
    def __init__(self, bot: AdminBot) -> None:
        super().__init__(bot)
        bot.jobs.register(JobKind.GROUPS_CREATE, self.create_item, audit_action="groups.create")
        bot.jobs.register(JobKind.GROUPS_ASSIGN, self.assign_item, audit_action="groups.assign")
        bot.jobs.register(JobKind.GROUPS_POST, self.post_item, on_finish=self.post_finish,
                          audit_action="groups.post")

    async def group_rooms(self, course: Course) -> dict[int, RoomID]:
        rooms = {}
        for room_id in await self.bot.ops.children(course.space_id):
            tag = await self.bot.ops.tag(room_id) or {}
            if tag.get("kind") == "group" and isinstance(tag.get("group"), int):
                rooms[tag["group"]] = room_id
        return rooms

    async def require_groups(self, course: Course) -> dict[int, RoomID]:
        rooms = await self.group_rooms(course)
        if not rooms:
            raise ValidationError("err.no_groups", code=course.code)
        return rooms

    # --- create ---------------------------------------------------------------------------
    @command("groups", "create", usage="usage.groups.create", role=Role.TA)
    async def create(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.groups.create")
        a = split_args(args, {"dry-run": False}, usage)
        code, count_raw = a.require(2, usage)
        course = await self.require_course(evt, code, Role.TA)
        try:
            count = int(count_raw)
        except ValueError as e:
            raise ArgError("err.bad_args", usage=usage) from e
        if not 1 <= count <= MAX_NEW_GROUPS:
            raise ValidationError("err.group_count", max=MAX_NEW_GROUPS)
        existing = await self.group_rooms(course)
        start = max(existing, default=0) + 1
        numbers = list(range(start, start + count))
        items = [{"number": n, "label": t("msg.group_name", n=n)} for n in numbers]
        summary = t("msg.groups_create_plan", code=course.code, count=count, first=numbers[0], last=numbers[-1])
        context = {"course": course.code, "role": Role.TA.value}
        await self.propose(evt, JobKind.GROUPS_CREATE.value, summary, items, context, a.flag("dry-run"))

    async def create_item(self, job: Job, item: JobItem) -> None:
        course = await self.course(job.context["course"])
        staff = await self.bot.store.get_staff(course.code)
        tas = [u for u, r in staff.items() if r == TA]
        users = templates.staff_power(course.owner, tas, self.levels, self.bot.client.mxid)
        await templates.create_group_room(
            self.bot.ops, course.space_id, course.code, item.payload["number"], t("msg.group_name_format"),
            users, [course.owner, *tas], encryption=True,
        )

    # --- assign ---------------------------------------------------------------------------
    @command("groups", "assign", usage="usage.groups.assign", role=Role.TA)
    async def assign(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.groups.assign")
        a = split_args(args, {"auto": False, "dry-run": False}, usage)
        course = await self.require_course(evt, a.require(1, usage)[0], Role.TA)
        rooms = await self.require_groups(course)
        if a.flag("auto"):
            staff = set(await self.bot.store.get_staff(course.code)) | {self.bot.client.mxid}
            students = sorted(await self.bot.ops.joined_members(course.space_id) - staff)
            if not students:
                raise ValidationError("err.no_students", code=course.code)
            numbers = sorted(rooms)
            split = round_robin(students, len(numbers))
            assignments = [(user, numbers[i - 1], "student") for i, users in split.items() for user in users]
            await self.plan_assign(evt, course, rooms, assignments, [], a.flag("dry-run"))
        else:
            await self.with_file(evt, self.on_assign_file, {"code": course.code, "dry_run": a.flag("dry-run")})

    async def on_assign_file(self, evt: MessageEvent, upload: Upload, context: dict[str, Any]) -> None:
        course = await self.require_course(evt, context["code"], Role.TA)
        rooms = await self.require_groups(course)
        assignments, invalid = [], []
        for identifier, group, role in parse_group_assignments(decode_text(upload.data)):
            try:
                user_id = self.identifier_to_user(identifier)
            except ValidationError:
                invalid.append(identifier)
                continue
            if group not in rooms:
                invalid.append(f"{identifier} ({t('msg.group_name', n=group)})")
                continue
            assignments.append((user_id, group, role))
        await self.plan_assign(evt, course, rooms, assignments, invalid, context["dry_run"])

    async def plan_assign(self, evt: MessageEvent, course: Course, rooms: dict[int, RoomID],
                          assignments: list[tuple[str, int, str]], invalid: list[str], dry_run: bool) -> None:
        if not assignments:
            raise ValidationError("err.nothing_to_assign")
        counts = Counter(group for _, group, _ in assignments)
        per_group = ", ".join(f"{t('msg.group_name', n=g)}: {counts[g]}" for g in sorted(counts))
        summary = t("msg.groups_assign_plan", code=course.code, count=len(assignments), per_group=per_group)
        if invalid:
            summary += "\n" + t("msg.invalid_entries", entries=list_preview(invalid))
        items = [
            {"user_id": user, "group": group, "role": role, "room_id": rooms[group], "label": f"{user} → {group}"}
            for user, group, role in assignments
        ]
        context = {"course": course.code, "role": Role.TA.value}
        await self.propose(evt, JobKind.GROUPS_ASSIGN.value, summary, items, context, dry_run)

    async def assign_item(self, job: Job, item: JobItem) -> None:
        room_id, user_id = item.payload["room_id"], item.payload["user_id"]
        if item.payload.get("role") == "ta":
            await self.bot.ops.set_user_power(room_id, user_id, self.levels["ta"])
        await self.bot.ops.invite(room_id, user_id)

    # --- post -----------------------------------------------------------------------------
    @command("groups", "post", usage="usage.groups.post", role=Role.TA)
    async def post(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.groups.post")
        a = split_args(args, usage=usage)
        course = await self.require_course(evt, a.require(1, usage)[0], Role.TA)
        await self.with_file(evt, self.on_post_file, {"code": course.code})

    async def on_post_file(self, evt: MessageEvent, upload: Upload, context: dict[str, Any]) -> None:
        course = await self.require_course(evt, context["code"], Role.TA)
        entries = parse_group_posts(decode_text(upload.data))
        rooms = await self.group_rooms(course) if any(kind == "group" for kind, _, _ in entries) else {}
        items, invalid = [], []
        for kind, target, message in entries:
            if kind == "group":
                number = int(target)
                if number not in rooms:
                    invalid.append(t("msg.group_name", n=number))
                    continue
                items.append({"kind": "room", "room_id": rooms[number], "message": message,
                              "label": t("msg.group_name", n=number)})
            else:
                try:
                    user_id = self.identifier_to_user(target)
                except ValidationError:
                    invalid.append(target)
                    continue
                items.append({"kind": "dm", "user_id": user_id, "message": message, "label": user_id})
        if not items:
            raise ValidationError("err.nothing_to_post")
        preview = "\n".join(f"> **{i['label']}**: {i['message'][:200]}" for i in items[:PREVIEW_POSTS])
        summary = t("msg.groups_post_plan", code=course.code, count=len(items), preview=preview)
        if invalid:
            summary += "\n" + t("msg.invalid_entries", entries=list_preview(invalid))
        # Posts are always previewed and confirmed (no dry-run needed).
        await self.propose(evt, JobKind.GROUPS_POST.value, summary, items,
                           {"course": course.code, "role": Role.TA.value}, dry_run=False)

    async def post_item(self, job: Job, item: JobItem) -> None:
        if item.payload["kind"] == "room":
            room_id = item.payload["room_id"]
        else:
            room_id = await self.bot.ops.open_dm(item.payload["user_id"])
        await self.bot.ops.send_text(room_id, item.payload["message"])

    async def post_finish(self, job: Job) -> None:
        """The messages may contain grades: don't keep them in the job table."""
        await self.bot.store.scrub_job_items(job.id)
