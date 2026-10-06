"""Command plumbing: the @command decorator, the shared CommandGroup helpers and the router.

maubot's own command decorators answer with usage help to anyone, in any room, before a handler
can check permissions. The bot is a member of every course room, so a single router handles
all messages instead: unknown senders and non-DM rooms are dealt with before anything is said.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from maubot import MessageEvent
from maubot.handlers import event
from mautrix.errors import MatrixRequestError
from mautrix.types import EventID, EventType, MessageType, RoomID, UserID

from ..attachments import Upload, UploadHandler, download, file_from_reply, is_file_message, send_file
from ..authz import Role
from ..db import Course
from ..errors import AdminBotError, NotFound, ValidationError
from ..identity import IdentityError, is_local_user, normalize_email, user_id_for_email
from ..strings import t

if TYPE_CHECKING:
    from ..bot import AdminBot

log = logging.getLogger("maubot.adminbot.commands")

COMMAND_RE = re.compile(r"^!(\S+)\s*(.*)$", re.DOTALL)
WORD_RE = re.compile(r"^(\S+)\s*(.*)$", re.DOTALL)

Handler = Callable[[MessageEvent, str], Awaitable[None]]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    sub: str | None
    usage: str  # strings key, shown in !help and on errors
    role: Role | None  # who sees it in !help (None = every known user); handlers check for real
    needs: tuple[str, ...]  # startup checks the command depends on


def command(name: str, sub: str | None = None, *, usage: str, role: Role | None,
            needs: tuple[str, ...] = ("admin",)) -> Callable[[Handler], Handler]:
    def decorator(func: Handler) -> Handler:
        func.__adminbot_command__ = CommandSpec(name, sub, usage, role, needs)
        return func

    return decorator


class CommandGroup:
    def __init__(self, bot: AdminBot) -> None:
        self.bot = bot

    # --- replies ------------------------------------------------------------------------
    async def reply(self, evt: MessageEvent, key: str, **params: object) -> EventID:
        return await self.bot.ops.send(evt.room_id, t(key, **params))

    async def reply_text(self, evt: MessageEvent, text: str) -> EventID:
        return await self.bot.ops.send(evt.room_id, text)

    async def reply_file(self, evt: MessageEvent, filename: str, data: bytes, mimetype: str) -> EventID:
        return await send_file_to(self.bot, evt.room_id, filename, data, mimetype)

    # --- permissions and lookups -----------------------------------------------------------
    async def require(self, evt: MessageEvent, role: Role | None, course: str | None = None) -> None:
        await self.bot.authz.require(evt.sender, role, course)

    async def course(self, code: str | None) -> Course:
        course = await self.bot.store.get_course((code or "").upper())
        if course is None:
            raise NotFound("err.unknown_course", code=code or "")
        return course

    async def require_course(self, evt: MessageEvent, code: str | None, role: Role) -> Course:
        course = await self.course(code)
        await self.require(evt, role, course.code)
        return course

    def user_arg(self, value: str | None) -> UserID:
        """A local Matrix ID like @max.muster:server."""
        if not value or not is_local_user(value, self.bot.server_name):
            raise ValidationError("err.bad_user", value=value or "", server=self.bot.server_name)
        return UserID(value)

    def email_to_user(self, value: str) -> tuple[UserID, str]:
        """(user ID, normalised email) for an allowed university address."""
        try:
            email = normalize_email(value)
            return user_id_for_email(email, self.bot.server_name, self.bot.config["identity.allowed_domains"]), email
        except IdentityError as e:
            raise ValidationError(e.key, value=e.value) from e

    def identifier_to_user(self, value: str) -> UserID:
        """Email or Matrix ID."""
        value = value.strip()
        return self.user_arg(value) if value.startswith("@") else self.email_to_user(value)[0]

    @property
    def levels(self) -> dict[str, int]:
        return {"owner": self.bot.config["power_levels.owner"], "ta": self.bot.config["power_levels.ta"]}

    # --- confirm and files -----------------------------------------------------------------
    async def propose(self, evt: MessageEvent, kind: str, summary: str, items: list[dict[str, Any]],
                      context: dict[str, Any], dry_run: bool) -> None:
        if dry_run:
            await self.reply(evt, "msg.dry_run", summary=summary)
            return
        plan = await self.bot.confirm.propose(evt.sender, evt.room_id, kind, summary, items, context)
        await self.reply(evt, "msg.plan", summary=summary, code=plan.code,
                         minutes=self.bot.config["confirm.ttl_minutes"])

    @property
    def max_upload(self) -> int:
        return self.bot.config["uploads.max_size_mb"] * 1024 * 1024

    async def with_file(self, evt: MessageEvent, handler: UploadHandler, context: dict[str, Any]) -> None:
        """Use the file the command replies to, or wait for the next upload in this DM."""
        upload = await file_from_reply(self.bot.client, evt, self.max_upload)
        if upload is not None:
            await handler(evt, upload, context)
            return
        self.bot.uploads.expect(evt.room_id, evt.sender, handler, context)
        await self.reply(evt, "msg.waiting_for_file", minutes=self.bot.config["uploads.wait_minutes"])


async def send_file_to(bot: AdminBot, room_id: RoomID, filename: str, data: bytes, mimetype: str) -> EventID:
    return await send_file(bot.client, room_id, filename, data, mimetype, await bot.ops.is_encrypted(room_id))


def describe(e: Exception) -> str:
    if isinstance(e, AdminBotError):
        return str(e)
    if isinstance(e, MatrixRequestError):
        return t("err.matrix", detail=getattr(e, "message", None) or str(e))
    return t("err.internal")


class CommandRouter:
    """The only Matrix event handler: routes `!command sub args` and uploaded files."""

    def __init__(self, bot: AdminBot, groups: list[CommandGroup]) -> None:
        self.bot = bot
        self.handlers: dict[tuple[str, str | None], tuple[CommandSpec, Handler]] = {}
        self.specs: list[CommandSpec] = []
        for group in groups:
            for attr in dir(type(group)):
                spec = getattr(getattr(type(group), attr), "__adminbot_command__", None)
                if spec is not None:
                    self.handlers[(spec.name, spec.sub)] = (spec, getattr(group, attr))
                    self.specs.append(spec)
        order = {name: i for i, name in enumerate(
            ["help", "whoami", "confirm", "cancel", "course", "room", "invite", "roster", "groups",
             "template", "signup", "bot", "admin"])}
        self.specs.sort(key=lambda s: (order.get(s.name, 99), s.sub or ""))
        self.names = {spec.name for spec in self.specs}

    async def _allowed_sender(self, sender: UserID) -> bool:
        return (
            sender != self.bot.client.mxid
            and is_local_user(sender, self.bot.server_name)
            and await self.bot.authz.is_known(sender)
        )

    async def _in_dm(self, evt: MessageEvent) -> bool:
        if evt.room_id == self.bot.config["audit_room"]:
            return False
        return await self.bot.ops.is_dm(evt.room_id, evt.sender)

    @event.on(EventType.ROOM_MESSAGE)
    async def on_message(self, evt: MessageEvent) -> None:
        if evt.sender == self.bot.client.mxid:
            return
        if is_file_message(evt.content):
            await self.on_file(evt)
            return
        if evt.content.msgtype != MessageType.TEXT or not evt.content.body:
            return
        evt.content.trim_reply_fallback()
        match = COMMAND_RE.match(evt.content.body.strip())
        if not match or match.group(1).lower() not in self.names:
            return
        name, rest = match.group(1).lower(), match.group(2)
        if not await self._allowed_sender(evt.sender):
            return
        if not await self._in_dm(evt):
            await self.bot.ops.send(evt.room_id, t("err.not_dm"))
            return
        try:
            await self.bot.ops.remember_dm(evt.sender, evt.room_id)
        except Exception as e:
            log.warning(f"Couldn't record DM of {evt.sender}: {e}")
        sub_match = WORD_RE.match(rest)
        sub = sub_match.group(1).lower() if sub_match else None
        if sub is not None and (name, sub) in self.handlers:
            spec, handler = self.handlers[(name, sub)]
            args = sub_match.group(2)
        elif (name, None) in self.handlers:
            spec, handler = self.handlers[(name, None)]
            args = rest
        else:
            usages = "\n".join(f"- {t(s.usage)}" for s in self.specs if s.name == name)
            await self.bot.ops.send(evt.room_id, t("msg.subcommands", name=name, usages=usages))
            return
        failed = [check for check in spec.needs if check in self.bot.failed_checks]
        if failed:
            await self.bot.ops.send(evt.room_id, t("err.check_failed", check=", ".join(failed)))
            return
        await self._run(evt, handler(evt, args), spec)

    async def on_file(self, evt: MessageEvent) -> None:
        if not self.bot.uploads.is_waiting(evt.room_id, evt.sender):
            return
        if not await self._allowed_sender(evt.sender) or not await self._in_dm(evt):
            return
        waiting = self.bot.uploads.take(evt.room_id, evt.sender)
        if waiting is None:
            return

        async def process() -> None:
            upload: Upload = await download(self.bot.client, evt.content, self.bot.config["uploads.max_size_mb"] * 1024 * 1024)
            await waiting.handler(evt, upload, waiting.context)

        await self._run(evt, process(), None)

    async def _run(self, evt: MessageEvent, coro: Awaitable[None], spec: CommandSpec | None) -> None:
        try:
            await coro
        except AdminBotError as e:
            await self.bot.ops.send(evt.room_id, describe(e))
        except MatrixRequestError as e:
            log.warning(f"Matrix error in {spec.name if spec else 'upload'}: {e}")
            await self.bot.ops.send(evt.room_id, describe(e))
        except Exception:
            log.exception(f"Command {spec.name if spec else 'upload'} failed")
            await self.bot.ops.send(evt.room_id, t("err.internal"))
