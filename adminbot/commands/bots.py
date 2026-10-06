"""!bot: professor-owned bots (plan §10). Every handler needs the 'userbots' startup check."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maubot import MessageEvent
from mautrix.types import UserID

from ..attachments import Upload, file_from_reply
from ..authz import Role
from ..errors import Forbidden, ValidationError
from ..jobs import JobKind
from ..parsing import Args, decode_text, split_args
from ..strings import t
from ..userbots import BotManager
from .base import CommandGroup, command

if TYPE_CHECKING:
    from ..bot import AdminBot

NEEDS = ("admin", "userbots")


class BotCommands(CommandGroup):
    def __init__(self, bot: AdminBot) -> None:
        super().__init__(bot)
        bot.jobs.register(JobKind.BOT_PROVISION, self.provision_item, audit_action="bot.provision")
        bot.jobs.register(JobKind.BOT_DELETE, self.delete_item, audit_action="bot.delete")

    @property
    def manager(self) -> BotManager:
        if self.bot.bots is None:
            raise ValidationError("err.bots_disabled")
        return self.bot.bots

    async def provision_item(self, job, item) -> None:
        await self.manager.provision_item(job, item)

    async def delete_item(self, job, item) -> None:
        await self.manager.delete_item(job, item)

    async def target(self, evt: MessageEvent, a: Args, usage: str) -> tuple[UserID, str]:
        """`<name>` (own bot) or `<@owner> <name>` (admins)."""
        await self.require(evt, Role.PROFESSOR)
        if a.get(0, "").startswith("@"):
            owner = self.user_arg(a.get(0))
            name = a.require(2, usage)[1]
            if owner != evt.sender and not self.bot.authz.is_admin(evt.sender):
                raise Forbidden("err.forbidden")
        else:
            owner, name = evt.sender, a.require(1, usage)[0]
        return owner, BotManager.check_name(name)

    @command("bot", "create", usage="usage.bot.create", role=Role.PROFESSOR, needs=NEEDS)
    async def create(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.bot.create")
        await self.require(evt, Role.PROFESSOR)
        name = BotManager.check_name(split_args(args, usage=usage).require(1, usage)[0])
        _ = self.manager  # raises if professor bots are disabled
        if await self.bot.store.get_user_bot(evt.sender, name):
            raise ValidationError("err.bot_exists", name=name)
        await self.with_file(evt, self.on_mbp, {"action": "create", "owner": evt.sender, "name": name})

    @command("bot", "update", usage="usage.bot.update", role=Role.PROFESSOR, needs=NEEDS)
    async def update(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.bot.update")
        owner, name = await self.target(evt, split_args(args, usage=usage), usage)
        await self.manager.get(owner, name)
        await self.with_file(evt, self.on_mbp, {"action": "update", "owner": owner, "name": name})

    async def on_mbp(self, evt: MessageEvent, upload: Upload, context: dict[str, Any]) -> None:
        await self.require(evt, Role.PROFESSOR)
        request = await self.manager.request(context["owner"], context["name"], upload.data, context["action"],
                                             evt.sender)
        if request.state == "pending":
            await self.reply(evt, "msg.bot_requested", name=request.name, id=request.id)
        else:
            await self.reply(evt, "msg.bot_provisioning", name=request.name)

    @command("bot", "list", usage="usage.bot.list", role=Role.PROFESSOR, needs=NEEDS)
    async def list_(self, evt: MessageEvent, args: str) -> None:
        await self.require(evt, Role.PROFESSOR)
        owner = None if self.bot.authz.is_admin(evt.sender) else evt.sender
        bots = await self.manager.list_for(owner)
        lines = [f"- `{b.name}` ({b.owner}): {b.user_id}, {b.plugin_id}, {b.state}" for b in bots]
        await self.reply(evt, "msg.bot_list", bots="\n".join(lines) or "-")

    @command("bot", "info", usage="usage.bot.info", role=Role.PROFESSOR, needs=NEEDS)
    async def info(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.bot.info")
        owner, name = await self.target(evt, split_args(args, usage=usage), usage)
        info = await self.manager.info(owner, name)
        await self.reply(evt, "msg.bot_info", **info,
                         status=t("msg.bot_running") if info["started"] else t("msg.bot_stopped"))

    @command("bot", "config", usage="usage.bot.config", role=Role.PROFESSOR, needs=NEEDS)
    async def config(self, evt: MessageEvent, args: str) -> None:
        """Without a replied-to file: send the config. Replying to a .yaml: replace it."""
        usage = t("usage.bot.config")
        owner, name = await self.target(evt, split_args(args, usage=usage), usage)
        upload = await file_from_reply(self.bot.client, evt, self.max_upload)
        if upload is None:
            text = await self.manager.get_config(owner, name)
            await self.reply_file(evt, f"{name}-config.yaml", text.encode("utf-8"), "application/yaml")
            return
        await self.manager.set_config(owner, name, decode_text(upload.data))
        await self.bot.audit.log(evt.sender, "bot.config", bot=name)
        await self.reply(evt, "msg.bot_config_saved", name=name)

    async def _set_started(self, evt: MessageEvent, args: str, started: bool, usage_key: str) -> None:
        usage = t(usage_key)
        owner, name = await self.target(evt, split_args(args, usage=usage), usage)
        await self.manager.set_started(owner, name, started)
        await self.bot.audit.log(evt.sender, "bot.start" if started else "bot.stop", bot=name)
        await self.reply(evt, "msg.bot_started" if started else "msg.bot_stopped_now", name=name)

    @command("bot", "start", usage="usage.bot.start", role=Role.PROFESSOR, needs=NEEDS)
    async def start(self, evt: MessageEvent, args: str) -> None:
        await self._set_started(evt, args, True, "usage.bot.start")

    @command("bot", "stop", usage="usage.bot.stop", role=Role.PROFESSOR, needs=NEEDS)
    async def stop(self, evt: MessageEvent, args: str) -> None:
        await self._set_started(evt, args, False, "usage.bot.stop")

    @command("bot", "delete", usage="usage.bot.delete", role=Role.PROFESSOR, needs=NEEDS)
    async def delete(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.bot.delete")
        owner, name = await self.target(evt, split_args(args, usage=usage), usage)
        bot = await self.manager.get(owner, name)
        summary = t("msg.bot_delete_plan", name=name, user_id=bot.user_id)
        items = [{"owner": owner, "name": name, "label": name}]
        await self.propose(evt, JobKind.BOT_DELETE.value, summary, items,
                           {"bot": name, "role": Role.PROFESSOR.value}, dry_run=False)
