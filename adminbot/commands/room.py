"""!room add / remove inside a course space."""
from __future__ import annotations

from maubot import MessageEvent

from .. import templates
from ..authz import TA, Role
from ..errors import NotFound
from ..parsing import split_args
from ..strings import t
from .base import CommandGroup, command


class RoomCommands(CommandGroup):
    @command("room", "add", usage="usage.room.add", role=Role.PROFESSOR)
    async def add(self, evt: MessageEvent, args: str) -> None:
        """Restricted to the space, or with --private invite-only for staff."""
        usage = t("usage.room.add")
        a = split_args(args, {"private": False, "topic": True}, usage)
        code, name = a.require(2, usage)
        course = await self.require_course(evt, code, Role.PROFESSOR)
        staff = await self.bot.store.get_staff(course.code)
        tas = [u for u, r in staff.items() if r == TA]
        users = templates.staff_power(course.owner, tas, self.levels, self.bot.client.mxid)
        position = 0
        for room_id in await self.bot.ops.children(course.space_id):
            tag = await self.bot.ops.tag(room_id) or {}
            if tag.get("kind") != "group":
                position += 1
        spec = templates.RoomSpec(name=name.strip(), topic=a.value("topic"),
                                  members="staff" if a.flag("private") else "all")
        room_id = await templates.create_spec_room(
            self.bot.ops, templates.Template(space_name=""), spec, course.space_id, course.code, users,
            [course.owner, *tas], f"{position:03d}",
        )
        await self.bot.audit.log(evt.sender, "room.add", course=course.code, target_count=1)
        await self.reply(evt, "msg.room_added", name=spec.name, code=course.code, room_id=room_id)

    @command("room", "remove", usage="usage.room.remove", role=Role.PROFESSOR)
    async def remove(self, evt: MessageEvent, args: str) -> None:
        """Removes the room from the space; the room itself and its history stay."""
        usage = t("usage.room.remove")
        a = split_args(args, usage=usage)
        code, target = a.require(2, usage)
        course = await self.require_course(evt, code, Role.PROFESSOR)
        for room_id in await self.bot.ops.children(course.space_id):
            settings = await self.bot.ops.room_settings(room_id)
            if room_id == target or (settings["name"] or "").lower() == target.lower():
                await self.bot.ops.remove_child(course.space_id, room_id)
                await self.bot.audit.log(evt.sender, "room.remove", course=course.code, target_count=1)
                await self.reply(evt, "msg.room_removed", name=settings["name"] or room_id, code=course.code)
                return
        raise NotFound("err.room_unknown", name=target, code=course.code)
