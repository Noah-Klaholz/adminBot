"""!signup: single links, guests, list, revoke, renew, password reset (plan §4)."""
from __future__ import annotations

from maubot import MessageEvent
from mautrix.types import UserID

from ..authz import Role
from ..db import SignupToken
from ..delivery import InlineDelivery
from ..errors import Forbidden, NotFound, ValidationError
from ..identity import IdentityError, guest_user_id, normalize_email
from ..parsing import split_args
from ..strings import t
from .base import CommandGroup, command


class SignupCommands(CommandGroup):
    @property
    def delivery(self) -> InlineDelivery:
        return InlineDelivery(self.bot.config["signup.login_url"])

    async def _issue(self, evt: MessageEvent, user_id: UserID, email: str, course_code: str | None) -> None:
        if await self.bot.synapse.user_exists(user_id):
            if course_code:
                course = await self.course(course_code)
                await self.bot.ops.invite(course.space_id, user_id)
                await self.bot.audit.log(evt.sender, "invite", course=course.code, target_count=1)
                await self.reply(evt, "msg.signup_exists_invited", user_id=user_id, code=course.code)
                return
            raise ValidationError("err.account_exists", user_id=user_id)
        link = await self.bot.signups.create(user_id, [email], evt.sender, course_code)
        await self.bot.audit.log(evt.sender, "signup.create", course=course_code, target_count=1)
        await self.reply_text(evt, self.delivery.deliver([link]).text)

    @command("signup", "new", usage="usage.signup.new", role=Role.TA)
    async def new(self, evt: MessageEvent, args: str) -> None:
        """Professor (no course), or TA of the course."""
        usage = t("usage.signup.new")
        a = split_args(args, usage=usage)
        email_raw = a.require(1, usage)[0]
        code = None
        if a.get(1):
            code = (await self.require_course(evt, a.get(1), Role.TA)).code
        else:
            await self.require(evt, Role.PROFESSOR)
        user_id, email = self.email_to_user(email_raw)
        await self._issue(evt, user_id, email, code)

    @command("signup", "guest", usage="usage.signup.guest", role=Role.ADMIN)
    async def guest(self, evt: MessageEvent, args: str) -> None:
        """Guests without a university address: any email domain, @guest.<name>."""
        await self.require(evt, Role.ADMIN)
        usage = t("usage.signup.guest")
        a = split_args(args, usage=usage)
        email_raw, name = a.require(2, usage)
        code = (await self.course(a.get(2))).code if a.get(2) else None
        try:
            email = normalize_email(email_raw)
            user_id = guest_user_id(name, self.bot.server_name)
        except IdentityError as e:
            raise ValidationError(e.key, value=e.value) from e
        await self._issue(evt, user_id, email, code)

    async def _visible_tokens(self, evt: MessageEvent, course_code: str | None) -> list[SignupToken]:
        if course_code:
            course = await self.require_course(evt, course_code, Role.TA)
            return await self.bot.signups.list_open(course.code)
        tokens = await self.bot.signups.list_open()
        if self.bot.authz.is_admin(evt.sender):
            return tokens
        await self.require(evt, Role.TA)
        return [tok for tok in tokens if await self._may_manage(evt.sender, tok)]

    async def _may_manage(self, sender: UserID, token: SignupToken) -> bool:
        if self.bot.authz.is_admin(sender) or token.created_by == sender:
            return True
        return bool(token.course) and await self.bot.authz.has(sender, Role.TA, token.course)

    @command("signup", "list", usage="usage.signup.list", role=Role.TA, needs=())
    async def list_(self, evt: MessageEvent, args: str) -> None:
        a = split_args(args, usage=t("usage.signup.list"))
        tokens = await self._visible_tokens(evt, a.get(0))
        lines = [
            t("msg.signup_line", user_id=tok.user_id, course=tok.course or "-",
              expires=f"{tok.expires_at:%Y-%m-%d}", by=tok.created_by)
            for tok in tokens
        ]
        await self.reply(evt, "msg.signup_list", links="\n".join(lines) or "-")

    async def _target(self, evt: MessageEvent, target: str) -> UserID:
        """Resolve email/@user and check the sender may manage that user's links."""
        user_id = self.identifier_to_user(target)
        tokens = await self.bot.signups.open_tokens(user_id)
        if not tokens:
            raise NotFound("err.no_open_link", user_id=user_id)
        for token in tokens:
            if await self._may_manage(evt.sender, token):
                return user_id
        raise Forbidden("err.forbidden")

    @command("signup", "revoke", usage="usage.signup.revoke", role=Role.TA)
    async def revoke(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.signup.revoke")
        user_id = await self._target(evt, split_args(args, usage=usage).require(1, usage)[0])
        count = await self.bot.signups.revoke(user_id, evt.sender)
        await self.reply(evt, "msg.signup_revoked", user_id=user_id, count=count)

    @command("signup", "renew", usage="usage.signup.renew", role=Role.TA)
    async def renew(self, evt: MessageEvent, args: str) -> None:
        usage = t("usage.signup.renew")
        user_id = await self._target(evt, split_args(args, usage=usage).require(1, usage)[0])
        link = await self.bot.signups.renew(user_id, evt.sender)
        await self.reply_text(evt, self.delivery.deliver([link]).text)

    @command("signup", "reset", usage="usage.signup.reset", role=Role.ADMIN)
    async def reset(self, evt: MessageEvent, args: str) -> None:
        """Admins only: whoever gets the link can take over the account."""
        await self.require(evt, Role.ADMIN)
        usage = t("usage.signup.reset")
        user_id = self.user_arg(split_args(args, usage=usage).require(1, usage)[0])
        if user_id == self.bot.client.mxid:
            raise ValidationError("err.reset_self")
        link = await self.bot.signups.create_reset(user_id, evt.sender)
        await self.reply_text(evt, self.delivery.deliver([link]).text)
