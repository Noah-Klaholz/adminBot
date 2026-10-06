"""Synapse admin API wrappers. All calls use the bot's own token via client.api.request and
mautrix's SynapseAdminPath; the bot reaches Synapse on the Docker network (http://synapse:8008).
"""
from __future__ import annotations

from typing import Any

import aiohttp
from maubot.matrix import MaubotMatrixClient
from mautrix.api import Method, SynapseAdminPath
from mautrix.errors import MatrixRequestError, MForbidden, MNotFound
from mautrix.types import RoomAlias, RoomID, UserID

from .errors import ValidationError


class SynapseAdmin:
    def __init__(self, client: MaubotMatrixClient, http: aiohttp.ClientSession) -> None:
        self.client = client
        self.http = http

    async def get_user(self, user_id: UserID) -> dict[str, Any] | None:
        """GET /_synapse/admin/v2/users/<user_id>; None if the user doesn't exist."""
        try:
            return await self.client.api.request(Method.GET, SynapseAdminPath.v2.users[user_id])
        except MNotFound:
            return None

    async def user_exists(self, user_id: UserID) -> bool:
        return await self.get_user(user_id) is not None

    async def is_server_admin(self, user_id: UserID) -> bool:
        try:
            user = await self.get_user(user_id)
        except MForbidden:
            return False
        return bool(user and user.get("admin"))

    async def create_user(
        self,
        user_id: UserID,
        password: str,
        displayname: str | None = None,
        emails: list[str] | None = None,
        user_type: str | None = None,
    ) -> None:
        """PUT /_synapse/admin/v2/users/<user_id>. That endpoint also *modifies* existing users,
        so refuse if the user exists already."""
        if await self.user_exists(user_id):
            raise ValidationError("err.account_exists", user_id=user_id)
        content: dict[str, Any] = {"password": password, "admin": False}
        if displayname:
            content["displayname"] = displayname
        if emails:
            content["threepids"] = [{"medium": "email", "address": e} for e in emails]
        if user_type:
            content["user_type"] = user_type
        await self.client.api.request(Method.PUT, SynapseAdminPath.v2.users[user_id], content)

    async def reset_password(self, user_id: UserID, new_password: str, logout_devices: bool = True) -> None:
        await self.client.api.request(
            Method.POST, SynapseAdminPath.v1.reset_password[user_id],
            {"new_password": new_password, "logout_devices": logout_devices},
        )

    async def reactivate(self, user_id: UserID, password: str) -> None:
        """A deleted professor bot whose name is used again gets its old account back."""
        await self.client.api.request(Method.PUT, SynapseAdminPath.v2.users[user_id],
                                      {"deactivated": False, "password": password})

    async def deactivate(self, user_id: UserID, erase: bool = False) -> None:
        await self.client.api.request(Method.POST, SynapseAdminPath.v1.deactivate[user_id], {"erase": erase})

    async def override_ratelimit(self, user_id: UserID) -> None:
        """0/0 means 'no limit' for this user."""
        await self.client.api.request(
            Method.POST, SynapseAdminPath.v1.users[user_id].override_ratelimit,
            {"messages_per_second": 0, "burst_count": 0},
        )

    async def force_join(self, user_id: UserID, room: RoomID | RoomAlias) -> None:
        """The bot must be in the room with invite power (always true for bot-created rooms)."""
        await self.client.api.request(Method.POST, SynapseAdminPath.v1.join[room], {"user_id": user_id})

    async def joined_room_count(self, user_id: UserID) -> int:
        resp = await self.client.api.request(Method.GET, SynapseAdminPath.v1.users[user_id].joined_rooms)
        return int(resp.get("total", len(resp.get("joined_rooms", []))))

    async def login(self, user_id: UserID, password: str, device_name: str) -> tuple[str, str]:
        """Client-server password login WITHOUT the bot's token -> (access_token, device_id)."""
        url = str(self.client.api.base_url).rstrip("/") + "/_matrix/client/v3/login"
        body = {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": user_id},
            "password": password,
            "initial_device_display_name": device_name,
        }
        async with self.http.post(url, json=body) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise MatrixRequestError(f"login failed: {resp.status} {data.get('error', '')}")
        return data["access_token"], data["device_id"]
