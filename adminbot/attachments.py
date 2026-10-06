"""File in / file out, including E2EE DMs.

How a command gets its file (both work):
1. Reply to an uploaded file with the command (e.g. reply `!template import hs26` to a .yaml).
2. Send the command first; the next file from the same sender in the same DM within
   uploads.wait_minutes is used (PendingUploads).

mautrix.crypto needs python-olm, which the maubot image has but a dev venv usually doesn't:
it is imported inside the functions, never at module level, so pytest can import this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Awaitable, Callable

from maubot import MessageEvent
from maubot.matrix import MaubotMatrixClient
from mautrix.types import (
    EventID,
    FileInfo,
    MediaMessageEventContent,
    MessageEvent as BaseMessageEvent,
    MessageType,
    RoomID,
    UserID,
)

from .errors import ValidationError

FILE_MSGTYPES = (MessageType.FILE,)


@dataclass
class Upload:
    filename: str
    mimetype: str | None
    data: bytes


# Called with the triggering event, the file, and the context stored by expect().
UploadHandler = Callable[[MessageEvent, Upload, dict[str, Any]], Awaitable[None]]


@dataclass
class Waiting:
    handler: UploadHandler
    context: dict[str, Any]
    expires_at: float


def is_file_message(content: Any) -> bool:
    return isinstance(content, MediaMessageEventContent) and content.msgtype in FILE_MSGTYPES


async def download(client: MaubotMatrixClient, content: MediaMessageEventContent, max_size: int) -> Upload:
    """Plain (`url`) or encrypted (`file`) media; size-checked before and after downloading."""
    declared = content.info.size if content.info and content.info.size else 0
    if declared > max_size:
        raise ValidationError("err.file_too_large", mb=max_size // (1024 * 1024))
    if content.file and content.file.url:
        from mautrix.crypto.attachments import decrypt_attachment

        ciphertext = await client.download_media(content.file.url)
        if len(ciphertext) > max_size:
            raise ValidationError("err.file_too_large", mb=max_size // (1024 * 1024))
        data = decrypt_attachment(ciphertext, content.file.key.key, content.file.hashes["sha256"], content.file.iv)
    elif content.url:
        data = await client.download_media(content.url)
    else:
        raise ValidationError("err.file_missing")
    if len(data) > max_size:
        raise ValidationError("err.file_too_large", mb=max_size // (1024 * 1024))
    filename = getattr(content, "filename", None) or content.body or "upload"
    mimetype = content.info.mimetype if content.info else None
    return Upload(filename=filename, mimetype=mimetype, data=data)


async def file_from_reply(client: MaubotMatrixClient, evt: MessageEvent, max_size: int) -> Upload | None:
    """If evt replies to a file message, download that file."""
    reply_to = evt.content.get_reply_to()
    if not reply_to:
        return None
    target = await client.get_event(evt.room_id, reply_to)
    if not isinstance(target, BaseMessageEvent) or not is_file_message(target.content):
        return None
    return await download(client, target.content, max_size)


async def send_file(client: MaubotMatrixClient, room_id: RoomID, filename: str, data: bytes, mimetype: str,
                    encrypted: bool) -> EventID:
    """Upload and send m.file; encrypt the file first if the room is encrypted."""
    content = MediaMessageEventContent(
        msgtype=MessageType.FILE, body=filename, info=FileInfo(mimetype=mimetype, size=len(data)),
    )
    if encrypted and client.crypto:
        from mautrix.crypto.attachments import encrypt_attachment

        ciphertext, file = encrypt_attachment(data)
        file.url = await client.upload_media(ciphertext, mime_type="application/octet-stream", filename=filename)
        content.file = file
    else:
        content.url = await client.upload_media(data, mime_type=mimetype, filename=filename)
    return await client.send_message(room_id, content)


class PendingUploads:
    def __init__(self, wait_minutes: int) -> None:
        self.wait_seconds = wait_minutes * 60
        self.waiting: dict[tuple[RoomID, UserID], Waiting] = {}

    def expect(self, room_id: RoomID, sender: UserID, handler: UploadHandler, context: dict[str, Any]) -> None:
        """Replace any earlier wait of this sender in this room."""
        self.waiting[(room_id, sender)] = Waiting(handler, context, time.monotonic() + self.wait_seconds)

    def is_waiting(self, room_id: RoomID, sender: UserID) -> bool:
        waiting = self.waiting.get((room_id, sender))
        return bool(waiting and waiting.expires_at > time.monotonic())

    def take(self, room_id: RoomID, sender: UserID) -> Waiting | None:
        """Pop the wait if present and not expired."""
        waiting = self.waiting.pop((room_id, sender), None)
        if waiting and waiting.expires_at > time.monotonic():
            return waiting
        return None

    def cancel(self, room_id: RoomID, sender: UserID) -> None:
        self.waiting.pop((room_id, sender), None)

    def expire(self) -> int:
        now = time.monotonic()
        expired = [key for key, w in self.waiting.items() if w.expires_at <= now]
        for key in expired:
            del self.waiting[key]
        return len(expired)
