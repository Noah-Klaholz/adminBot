"""!course: create, list, info, TAs, archive, rollover (plan §9, §12)."""
from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import TYPE_CHECKING

from maubot import MessageEvent
from mautrix.types import RoomID, UserID

from .. import templates
from ..authz import OWNER, TA, Role
from ..db import Course, Job, JobItem
from ..errors import NotFound, ValidationError
from ..jobs import JobKind
from ..parsing import ArgError, split_args
from ..strings import t
from .base import CommandGroup, command

if TYPE_CHECKING:
    from ..bot import AdminBot

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
SEMESTER_RE = re.compile(r"^(HS|FS)\d{2}$")
ARCHIVE_PREFIX = "[Archiv"


def normalize_code(code: str) -> str:
    code = (code or "").strip().upper()
    if not CODE_RE.match(code):
        raise ValidationError("err.course_code", code=code)
    return code


def normalize_semester(semester: str) -> str:
    semester = (semester or "").strip().upper()
    if not SEMESTER_RE.match(semester):
        raise ValidationError("err.semester", semester=semester)
    return semester


def current_semester(today: date | None = None) -> str:
    """HS = autumn semester (Aug-Jan), FS = spring semester (Feb-Jul)."""
    today = today or date.today()
    if today.month >= 8:
        return f"HS{today.year % 100:02d}"
    if today.month == 1:
        return f"HS{(today.year - 1) % 100:02d}"
    return f"FS{today.year % 100:02d}"


def matrix_to(room_id: RoomID, server: str) -> str:
    return f"https://matrix.to/#/{room_id}?via={server}"


class CourseCommands(CommandGroup):
    def __init__(self, bot: AdminBot) -> None:
        super().__init__(bot)
        bot.jobs.register(JobKind.COURSE_ARCHIVE, self.archive_item, on_finish=self.archive_finish,
                          audit_action="course.archive")

    async def load_template(self, evt: MessageEvent, name: str | None) -> templates.Template:
        if not name:
            return templates.default_template()
        text = await self.bot.store.get_template(name, evt.sender)
        if text is None:
            raise NotFound("err.template_unknown", name=name)
        return templates.parse(text)

    async def course_rooms(self, course: Course) -> list[RoomID]:
        """Space first, then all children."""
        return [course.space_id, *await self.bot.ops.children(course.space_id)]

    async def create_course(self, code: str, title: str, semester: str, owner: UserID, tas: list[UserID],
                            template: templates.Template) -> templates.AppliedCourse:
        rendered = templates.render(template, code, title, semester)
        applied = await templates.apply(self.bot.ops, rendered, code, owner, tas, self.levels)
        await self.bot.store.create_course(Course(
            code=code, title=title, semester=semester, space_id=applied.space_id, owner=owner,
            archived=False, created_at=datetime.now(timezone.utc),
        ))
        await self.bot.store.add_staff(code, owner, OWNER)
        for ta in tas:
            await self.bot.store.add_staff(code, ta, TA)
        return applied

    @command("course", "create", usage="usage.course.create", role=Role.PROFESSOR)
    async def create(self, evt: MessageEvent, args: str) -> None:
        await self.require(evt, Role.PROFESSOR)
        usage = t("usage.course.create")
        a = split_args(args, {"semester": True, "template": True}, usage)
        code_raw, title = a.require(2, usage)
        code = normalize_code(code_raw)
        title = title.strip()
        if not title:
            raise ArgError("err.bad_args", usage=usage)
        if await self.bot.store.get_course(code):
            raise ValidationError("err.course_exists", code=code)
        semester = normalize_semester(a.value("semester") or current_semester())
        template = await self.load_template(evt, a.value("template"))
        await self.reply(evt, "msg.course_creating", code=code)
        applied = await self.create_course(code, title, semester, evt.sender, [], template)
        await self.bot.audit.log(evt.sender, "course.create", course=code, target_count=len(applied.rooms))
        await self.reply(
            evt, "msg.course_created", code=code, link=matrix_to(applied.space_id, self.bot.server_name),
            rooms=", ".join(name for name, _ in applied.rooms),
        )

    @command("course", "list", usage="usage.course.list", role=Role.TA, needs=())
    async def list_(self, evt: MessageEvent, args: str) -> None:
        await self.require(evt, None)
        if self.bot.authz.is_admin(evt.sender):
            courses = await self.bot.store.list_courses()
        else:
            courses = await self.bot.store.list_courses(evt.sender)
        lines = []
        for course in courses:
            role = await self.bot.store.staff_role(course.code, evt.sender) or "admin"
            archived = t("msg.archived_marker") if course.archived else ""
            lines.append(f"- **{course.code}** {course.title} ({course.semester}){archived}, {role}")
        await self.reply(evt, "msg.course_list", courses="\n".join(lines) or "-")

    @command("course", "info", usage="usage.course.info", role=Role.TA)
    async def info(self, evt: MessageEvent, args: str) -> None:
        a = split_args(args, usage=t("usage.course.info"))
        course = await self.require_course(evt, a.require(1, t("usage.course.info"))[0], Role.TA)
        staff = await self.bot.store.get_staff(course.code)
        members = await self.bot.ops.joined_or_invited(course.space_id)
        students = members - set(staff) - {self.bot.client.mxid}
        rooms = []
        for room_id in await self.bot.ops.children(course.space_id):
            settings = await self.bot.ops.room_settings(room_id)
            rooms.append(settings["name"] or room_id)
        open_links = await self.bot.signups.list_open(course.code)
        await self.reply(
            evt, "msg.course_info", code=course.code, title=course.title, semester=course.semester,
            owner=course.owner, tas=", ".join(u for u, r in staff.items() if r == TA) or "-",
            students=len(students), rooms=", ".join(rooms) or "-", links=len(open_links),
            archived=t("msg.yes") if course.archived else t("msg.no"),
            link=matrix_to(course.space_id, self.bot.server_name),
        )

    @command("course", "ta", usage="usage.course.ta", role=Role.PROFESSOR)
    async def ta(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.course.ta")
        action, code, user_raw = split_args(args, usage=usage).require(3, usage)
        action = action.lower()
        if action not in ("add", "remove"):
            raise ArgError("err.bad_args", usage=usage)
        course = await self.require_course(evt, code, Role.PROFESSOR)
        user = self.user_arg(user_raw)
        rooms = await self.course_rooms(course)
        if action == "add":
            if user == course.owner:
                raise ValidationError("err.ta_is_owner")
            if not await self.bot.synapse.user_exists(user):
                raise NotFound("err.user_not_found", user_id=user)
            await self.bot.store.add_staff(course.code, user, TA)
            for room_id in rooms:
                await self.bot.ops.set_user_power(room_id, user, self.levels["ta"])
                await self.bot.ops.invite(room_id, user)
            await self.bot.audit.log(evt.sender, "course.ta.add", course=course.code, target_count=1)
            await self.reply(evt, "msg.ta_added", user_id=user, code=course.code)
        else:
            if await self.bot.store.staff_role(course.code, user) != TA:
                raise NotFound("err.not_ta", user_id=user, code=course.code)
            await self.bot.store.remove_staff(course.code, user)
            for room_id in rooms:
                await self.bot.ops.set_user_power(room_id, user, None)
                tag = await self.bot.ops.tag(room_id) or {}
                if tag.get("members") == "staff":
                    await self.bot.ops.kick(room_id, user, t("msg.kick_reason_ta"))
            await self.bot.audit.log(evt.sender, "course.ta.remove", course=course.code, target_count=1)
            await self.reply(evt, "msg.ta_removed", user_id=user, code=course.code)

    @command("course", "archive", usage="usage.course.archive", role=Role.PROFESSOR)
    async def archive(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.course.archive")
        a = split_args(args, {"kick-students": False, "dry-run": False}, usage)
        course = await self.require_course(evt, a.require(1, usage)[0], Role.PROFESSOR)
        if course.archived:
            raise ValidationError("err.course_archived", code=course.code)
        items = []
        for room_id in await self.course_rooms(course):
            settings = await self.bot.ops.room_settings(room_id)
            items.append({"room_id": room_id, "label": settings["name"] or room_id})
        kick = a.flag("kick-students")
        summary = t("msg.archive_plan", code=course.code, rooms=len(items),
                    kick=t("msg.archive_kick") if kick else "")
        context = {"course": course.code, "role": Role.PROFESSOR.value, "kick": kick,
                   "semester": course.semester, "staff": list(await self.bot.store.get_staff(course.code))}
        await self.propose(evt, JobKind.COURSE_ARCHIVE.value, summary, items, context, a.flag("dry-run"))

    async def archive_item(self, job: Job, item: JobItem) -> None:
        room_id = item.payload["room_id"]
        ops = self.bot.ops
        await ops.set_power_key(room_id, "events_default", self.levels["ta"])
        settings = await ops.room_settings(room_id)
        name = settings["name"] or ""
        if not name.startswith(ARCHIVE_PREFIX):
            await ops.rename(room_id, f"{ARCHIVE_PREFIX} {job.context['semester']}] {name}".strip())
        if job.context.get("kick"):
            protected = set(job.context.get("staff", [])) | {self.bot.client.mxid}
            for user in await ops.joined_or_invited(room_id) - protected:
                await ops.kick(room_id, user, t("msg.kick_reason_archive"))

    async def archive_finish(self, job: Job) -> None:
        course = await self.bot.store.get_course(job.context["course"])
        if course is None:
            return
        await self.bot.store.set_course_archived(course.code, True)
        archive_space = self.bot.config["archive_space"]
        if archive_space:
            await self.bot.ops.add_child(archive_space, course.space_id, suggested=False)

    @command("course", "rollover", usage="usage.course.rollover", role=Role.PROFESSOR)
    async def rollover(self, evt: MessageEvent, args: str) -> None:
        """Snapshot -> the old course becomes <code>-<old semester> -> a new course <code> for the
        new semester with the same layout, owner and TAs. No students."""
        usage = t("usage.course.rollover")
        code, new_semester = split_args(args, usage=usage).require(2, usage)
        course = await self.require_course(evt, code, Role.PROFESSOR)
        new_semester = normalize_semester(new_semester)
        if new_semester == course.semester:
            raise ValidationError("err.same_semester", semester=new_semester)
        old_code = f"{course.code}-{course.semester}"
        if await self.bot.store.get_course(old_code):
            raise ValidationError("err.course_exists", code=old_code)
        template = await templates.snapshot(self.bot.ops, course.space_id, course.code, course.title,
                                            course.semester)
        staff = await self.bot.store.get_staff(course.code)
        tas = [u for u, r in staff.items() if r == TA]
        await self.reply(evt, "msg.course_creating", code=course.code)
        await self.bot.store.rename_course(course.code, old_code)
        try:
            applied = await self.create_course(course.code, course.title, new_semester, course.owner, tas, template)
        except Exception:
            await self.bot.store.rename_course(old_code, course.code)
            raise
        await self.bot.audit.log(evt.sender, "course.rollover", course=course.code, target_count=len(applied.rooms))
        await self.reply(evt, "msg.rollover_done", code=course.code, semester=new_semester, old_code=old_code,
                         link=matrix_to(applied.space_id, self.bot.server_name))
