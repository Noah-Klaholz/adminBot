"""!template: export (store + download), import, list, delete (plan §9)."""
from __future__ import annotations

import re
from typing import Any

from maubot import MessageEvent

from .. import templates
from ..attachments import Upload
from ..authz import Role
from ..errors import NotFound, ValidationError
from ..parsing import decode_text, split_args
from ..strings import t
from .base import CommandGroup, command

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
YAML_MIME = "application/yaml"


def check_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise ValidationError("err.template_name", name=name or "")
    return name


class TemplateCommands(CommandGroup):
    @command("template", "export", usage="usage.template.export", role=Role.PROFESSOR)
    async def export(self, evt: MessageEvent, args: str) -> None:
        """<code> [<name>]: snapshot, save as a personal template and send the .yaml.
        --saved <name>: send a stored template again (its course may no longer exist)."""
        usage = t("usage.template.export")
        a = split_args(args, {"saved": True}, usage)
        saved = a.value("saved")
        if saved:
            await self.require(evt, Role.PROFESSOR)
            text = await self.bot.store.get_template(saved, evt.sender)
            if text is None:
                raise NotFound("err.template_unknown", name=saved)
            await self.reply_file(evt, f"{saved}.yaml", text.encode("utf-8"), YAML_MIME)
            return
        code = a.require(1, usage)[0]
        course = await self.require_course(evt, code, Role.PROFESSOR)
        name = check_name(a.get(1) or course.code)
        template = await templates.snapshot(self.bot.ops, course.space_id, course.code, course.title,
                                            course.semester)
        text = templates.dump(template)
        await self.bot.store.save_template(name, "personal", evt.sender, text)
        await self.bot.audit.log(evt.sender, "template.export", course=course.code)
        await self.reply(evt, "msg.template_exported", name=name, code=course.code)
        await self.reply_file(evt, f"{name}.yaml", text.encode("utf-8"), YAML_MIME)

    @command("template", "import", usage="usage.template.import", role=Role.PROFESSOR)
    async def import_(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.template.import")
        a = split_args(args, {"global": False}, usage)
        name = check_name(a.require(1, usage)[0])
        scope = "global" if a.flag("global") else "personal"
        await self.require(evt, Role.ADMIN if scope == "global" else Role.PROFESSOR)
        await self.with_file(evt, self.on_file, {"name": name, "scope": scope})

    async def on_file(self, evt: MessageEvent, upload: Upload, context: dict[str, Any]) -> None:
        await self.require(evt, Role.ADMIN if context["scope"] == "global" else Role.PROFESSOR)
        text = decode_text(upload.data)
        templates.parse(text)
        owner = evt.sender if context["scope"] == "personal" else None
        await self.bot.store.save_template(context["name"], context["scope"], owner, text)
        await self.bot.audit.log(evt.sender, f"template.import.{context['scope']}")
        await self.reply(evt, "msg.template_saved", name=context["name"], scope=t(f"msg.scope_{context['scope']}"))

    @command("template", "list", usage="usage.template.list", role=Role.PROFESSOR, needs=())
    async def list_(self, evt: MessageEvent, args: str) -> None:
        await self.require(evt, Role.PROFESSOR)
        rows = await self.bot.store.list_templates(evt.sender)
        lines = [f"- `{name}` ({t(f'msg.scope_{scope}')})" for name, scope in rows]
        await self.reply(evt, "msg.template_list", templates="\n".join(lines) or "-")

    @command("template", "delete", usage="usage.template.delete", role=Role.PROFESSOR, needs=())
    async def delete(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.template.delete")
        a = split_args(args, {"global": False}, usage)
        name = a.require(1, usage)[0]
        scope = "global" if a.flag("global") else "personal"
        await self.require(evt, Role.ADMIN if scope == "global" else Role.PROFESSOR)
        owner = evt.sender if scope == "personal" else None
        if not await self.bot.store.delete_template(name, scope, owner):
            raise NotFound("err.template_unknown", name=name)
        await self.bot.audit.log(evt.sender, f"template.delete.{scope}")
        await self.reply(evt, "msg.template_deleted", name=name)
