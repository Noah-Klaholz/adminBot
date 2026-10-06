"""!help and !whoami."""
from __future__ import annotations

from maubot import MessageEvent

from ..authz import Role
from ..strings import t
from .base import CommandGroup, command


class HelpCommands(CommandGroup):
    async def _visible(self, sender: str, role: Role | None) -> bool:
        authz = self.bot.authz
        if role is None or authz.is_admin(sender):
            return True
        if role == Role.ADMIN:
            return False
        is_prof = await self.bot.store.is_professor(sender)
        if role == Role.PROFESSOR:
            return is_prof
        return is_prof or await self.bot.store.is_staff_anywhere(sender)

    @command("help", usage="usage.help", role=None, needs=())
    async def help(self, evt: MessageEvent, args: str) -> None:
        """Lists only the commands the sender's roles allow."""
        lines = [t("msg.help_header")]
        for spec in self.bot.router.specs:
            if await self._visible(evt.sender, spec.role):
                lines.append(f"- {t(spec.usage)}")
        lines.append(t("msg.help_footer"))
        await self.reply_text(evt, "\n".join(lines))

    @command("whoami", usage="usage.whoami", role=None, needs=())
    async def whoami(self, evt: MessageEvent, args: str) -> None:
        roles = await self.bot.authz.roles(evt.sender)
        await self.reply(
            evt, "msg.whoami", user_id=evt.sender,
            roles=", ".join(roles["global"]) or "-",
            courses=", ".join(roles["courses"]) or "-",
        )
