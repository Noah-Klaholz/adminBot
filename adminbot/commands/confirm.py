"""!confirm and !cancel for plans proposed by bulk/destructive commands."""
from __future__ import annotations

from maubot import MessageEvent

from ..authz import Role
from ..parsing import split_args
from ..strings import t
from .base import CommandGroup, command


class ConfirmCommands(CommandGroup):
    @command("confirm", usage="usage.confirm", role=None)
    async def confirm(self, evt: MessageEvent, args: str) -> None:
        """Only the requester. Roles are checked again: they may have changed since the plan."""
        code = split_args(args, usage=t("usage.confirm")).require(1, t("usage.confirm"))[0]
        plan = await self.bot.confirm.confirm(code, evt.sender)
        role = plan.context.get("role")
        await self.require(evt, Role(role) if role else None, plan.context.get("course"))
        job_id = await self.bot.jobs.submit(plan)
        if not plan.items:
            await self.reply(evt, "msg.job_done_instantly", id=job_id)

    @command("cancel", usage="usage.cancel", role=None, needs=())
    async def cancel(self, evt: MessageEvent, args: str) -> None:
        dropped = await self.bot.confirm.cancel(evt.sender)
        waiting = self.bot.uploads.is_waiting(evt.room_id, evt.sender)
        self.bot.uploads.cancel(evt.room_id, evt.sender)
        await self.reply(evt, "msg.cancelled" if dropped or waiting else "msg.nothing_to_cancel")
