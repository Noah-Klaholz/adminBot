"""Client-server operations: spaces, rooms, power levels, hierarchy, DMs, messages.

State is read and written as raw JSON (dicts), so custom event types like our room tag need no
special handling. Every room and space the bot creates carries a TAG_TYPE state event, which
is how the bot recognises course rooms, staff rooms and group rooms later.
"""
from __future__ import annotations

from typing import Any

from maubot.matrix import MaubotMatrixClient
from mautrix.api import ClientPath, Method
from mautrix.errors import MatrixRequestError, MNotFound
from mautrix.types import EventID, EventType, MessageType, RoomCreatePreset, RoomID, UserID

TAG_TYPE = "ch.unibas.dmi.marvin.room"
ENCRYPTION_CONTENT = {"algorithm": "m.megolm.v1.aes-sha2"}

# Room-level power settings the bot uses for every room it creates. Templates only store
# deviations from these (templates.PL_KEYS).
BASE_POWER = {
    "users_default": 0,
    "events_default": 0,
    "state_default": 50,
    "invite": 50,
    "kick": 50,
    "ban": 50,
    "redact": 50,
}
BASE_EVENT_POWER = {
    "m.room.power_levels": 100,
    "m.room.history_visibility": 100,
    "m.room.encryption": 100,
    "m.room.tombstone": 100,
    "m.room.server_acl": 100,
    "m.space.child": 100,
    "m.space.parent": 100,
    TAG_TYPE: 100,
}


def is_dm_members(joined: set[UserID], invited: set[UserID], bot: UserID, sender: UserID) -> bool:
    """Pure DM rule (plan §2): exactly {bot, sender} joined and nobody else invited."""
    return sender != bot and joined == {bot, sender} and not invited


def power_content(users: dict[UserID, int], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    content: dict[str, Any] = {**BASE_POWER, "users": dict(users), "events": dict(BASE_EVENT_POWER)}
    for key, value in (overrides or {}).items():
        content[key] = value
    return content


def _state_type(name: str) -> EventType:
    return EventType.find(name, t_class=EventType.Class.STATE)


class MatrixOps:
    def __init__(self, client: MaubotMatrixClient, server_name: str) -> None:
        self.client = client
        self.server_name = server_name
        self._dm_cache: dict[UserID, RoomID] = {}

    @property
    def mxid(self) -> UserID:
        return self.client.mxid

    # --- raw state helpers ------------------------------------------------------------------
    async def state(self, room_id: RoomID) -> list[dict[str, Any]]:
        return await self.client.api.request(Method.GET, ClientPath.v3.rooms[room_id].state)

    async def state_event(self, room_id: RoomID, event_type: str, state_key: str = "") -> dict[str, Any] | None:
        try:
            return await self.client.api.request(
                Method.GET, ClientPath.v3.rooms[room_id].state[event_type][state_key]
            )
        except MNotFound:
            return None

    async def put_state(self, room_id: RoomID, event_type: str, content: dict[str, Any],
                        state_key: str = "") -> EventID:
        return await self.client.send_state_event(room_id, _state_type(event_type), content, state_key)

    async def tag(self, room_id: RoomID) -> dict[str, Any] | None:
        """Our tag on rooms the bot created, None for everything else."""
        try:
            return await self.state_event(room_id, TAG_TYPE)
        except MatrixRequestError:
            return None

    async def is_encrypted(self, room_id: RoomID) -> bool:
        encrypted = None
        if self.client.state_store is not None:
            encrypted = await self.client.state_store.is_encrypted(room_id)
        if encrypted is None:
            encrypted = await self.state_event(room_id, "m.room.encryption") is not None
        return bool(encrypted)

    # --- membership -----------------------------------------------------------------------
    async def members(self, room_id: RoomID) -> dict[UserID, str]:
        """user ID -> membership (join, invite, leave, ban, knock)."""
        resp = await self.client.api.request(Method.GET, ClientPath.v3.rooms[room_id].members)
        return {evt["state_key"]: evt["content"].get("membership") for evt in resp.get("chunk", [])}

    async def joined_members(self, room_id: RoomID) -> set[UserID]:
        return {user for user, membership in (await self.members(room_id)).items() if membership == "join"}

    async def joined_or_invited(self, room_id: RoomID) -> set[UserID]:
        members = await self.members(room_id)
        return {user for user, membership in members.items() if membership in ("join", "invite")}

    async def invite(self, room_id: RoomID, user_id: UserID) -> None:
        """Ignores 'already joined/invited'."""
        try:
            await self.client.invite_user(room_id, user_id)
        except MatrixRequestError as e:
            if "already" in (getattr(e, "message", None) or str(e)).lower():
                return
            raise

    async def kick(self, room_id: RoomID, user_id: UserID, reason: str) -> bool:
        """Kick if joined or invited. Returns False if the user wasn't in the room."""
        membership = (await self.members(room_id)).get(user_id)
        if membership not in ("join", "invite"):
            return False
        await self.client.kick_user(room_id, user_id, reason=reason)
        return True

    # --- DMs and messages ---------------------------------------------------------------
    async def is_dm(self, room_id: RoomID, sender: UserID) -> bool:
        """Current members (not cached) must be exactly bot + sender, and the room must not be one
        of the bot's course rooms (those can have two members before students join)."""
        members = await self.members(room_id)
        joined = {u for u, m in members.items() if m == "join"}
        invited = {u for u, m in members.items() if m == "invite"}
        if not is_dm_members(joined, invited, self.mxid, sender):
            return False
        return await self.tag(room_id) is None

    async def _direct(self) -> dict[str, list[RoomID]]:
        try:
            return dict(await self.client.get_account_data(EventType.DIRECT) or {})
        except MNotFound:
            return {}

    async def remember_dm(self, user_id: UserID, room_id: RoomID) -> None:
        """Record a DM the user opened (the router calls this for every valid command), so
        notifications later go there instead of into a new room."""
        if self._dm_cache.get(user_id) == room_id:
            return
        direct = await self._direct()
        rooms = direct.get(user_id, [])
        if not rooms or rooms[0] != room_id:
            direct[user_id] = [room_id, *[r for r in rooms if r != room_id]]
            await self.client.set_account_data(EventType.DIRECT, direct)
        self._dm_cache[user_id] = room_id

    async def open_dm(self, user_id: UserID) -> RoomID:
        """Most recent DM with user_id (from m.direct) or a new one. Encrypted if the bot can encrypt."""
        direct = await self._direct()
        for room_id in direct.get(user_id, []):
            try:
                members = await self.members(room_id)
            except MatrixRequestError:
                continue
            others = {u for u, m in members.items() if m in ("join", "invite") and u != self.mxid}
            if members.get(self.mxid) == "join" and others == {user_id}:
                return room_id
        initial_state = []
        if self.client.crypto:
            initial_state.append({"type": "m.room.encryption", "state_key": "", "content": ENCRYPTION_CONTENT})
        room_id = await self.client.create_room(
            preset=RoomCreatePreset.TRUSTED_PRIVATE, is_direct=True, invitees=[user_id],
            initial_state=initial_state,
        )
        direct[user_id] = [room_id, *direct.get(user_id, [])]
        await self.client.set_account_data(EventType.DIRECT, direct)
        self._dm_cache[user_id] = room_id
        return room_id

    async def send(self, room_id: RoomID, markdown: str, edits: EventID | None = None) -> EventID:
        """Markdown notice (m.notice: other bots don't react to it)."""
        return await self.client.send_markdown(room_id, markdown, msgtype=MessageType.NOTICE, edits=edits)

    async def send_text(self, room_id: RoomID, markdown: str) -> EventID:
        """Normal message (m.text), for posts written by staff (!groups post)."""
        return await self.client.send_markdown(room_id, markdown)

    # --- spaces and rooms -----------------------------------------------------------------
    async def create_space(self, name: str, topic: str, power_users: dict[UserID, int],
                           invitees: list[UserID], tag: dict[str, Any]) -> RoomID:
        return await self.client.create_room(
            name=name,
            topic=topic or None,
            preset=RoomCreatePreset.PRIVATE,
            creation_content={"type": "m.space"},
            power_level_override=power_content(power_users),
            invitees=[u for u in invitees if u != self.mxid],
            initial_state=[
                {"type": "m.room.history_visibility", "state_key": "", "content": {"history_visibility": "shared"}},
                {"type": TAG_TYPE, "state_key": "", "content": tag},
            ],
        )

    async def create_room(self, name: str, topic: str | None, *, space_id: RoomID | None,
                          join_rule: str, encryption: bool, history_visibility: str,
                          power_users: dict[UserID, int], power_overrides: dict[str, Any],
                          invitees: list[UserID], tag: dict[str, Any]) -> RoomID:
        """join_rule 'restricted' allows members of space_id."""
        if join_rule == "restricted":
            join_content: dict[str, Any] = {
                "join_rule": "restricted",
                "allow": [{"type": "m.room_membership", "room_id": space_id}],
            }
        else:
            join_content = {"join_rule": join_rule}
        initial_state: list[dict[str, Any]] = [
            {"type": "m.room.join_rules", "state_key": "", "content": join_content},
            {"type": "m.room.history_visibility", "state_key": "", "content": {"history_visibility": history_visibility}},
            {"type": TAG_TYPE, "state_key": "", "content": tag},
        ]
        if encryption:
            initial_state.append({"type": "m.room.encryption", "state_key": "", "content": ENCRYPTION_CONTENT})
        if space_id:
            initial_state.append({
                "type": "m.space.parent", "state_key": space_id,
                "content": {"via": [self.server_name], "canonical": True},
            })
        return await self.client.create_room(
            name=name,
            topic=topic or None,
            preset=RoomCreatePreset.PRIVATE,
            power_level_override=power_content(power_users, power_overrides),
            invitees=[u for u in invitees if u != self.mxid],
            initial_state=initial_state,
        )

    async def add_child(self, space_id: RoomID, room_id: RoomID, order: str | None = None,
                        suggested: bool = True) -> None:
        content: dict[str, Any] = {"via": [self.server_name], "suggested": suggested}
        if order:
            content["order"] = order
        await self.put_state(space_id, "m.space.child", content, room_id)

    async def remove_child(self, space_id: RoomID, room_id: RoomID) -> None:
        await self.put_state(space_id, "m.space.child", {}, room_id)

    async def children(self, space_id: RoomID) -> list[RoomID]:
        """Direct children in m.space.child order (then by time added)."""
        entries = []
        for evt in await self.state(space_id):
            if evt.get("type") == "m.space.child" and evt.get("content", {}).get("via"):
                order = evt["content"].get("order")
                order = order if isinstance(order, str) else "\x7e"
                entries.append((order, evt.get("origin_server_ts", 0), evt["state_key"]))
        return [room for _, _, room in sorted(entries)]

    async def room_settings(self, room_id: RoomID) -> dict[str, Any]:
        """name, topic, join_rule, encryption, history_visibility, power_levels, tag."""
        by_type = {(e["type"], e.get("state_key", "")): e.get("content", {}) for e in await self.state(room_id)}
        return {
            "name": by_type.get(("m.room.name", ""), {}).get("name", ""),
            "topic": by_type.get(("m.room.topic", ""), {}).get("topic"),
            "join_rule": by_type.get(("m.room.join_rules", ""), {}).get("join_rule", "invite"),
            "encryption": bool(by_type.get(("m.room.encryption", ""))),
            "history_visibility": by_type.get(("m.room.history_visibility", ""), {}).get(
                "history_visibility", "shared"),
            "power_levels": by_type.get(("m.room.power_levels", ""), {}),
            "tag": by_type.get((TAG_TYPE, "")) or None,
        }

    async def rename(self, room_id: RoomID, name: str) -> None:
        await self.put_state(room_id, "m.room.name", {"name": name})

    async def set_topic(self, room_id: RoomID, topic: str) -> None:
        await self.put_state(room_id, "m.room.topic", {"topic": topic})

    async def set_user_power(self, room_id: RoomID, user_id: UserID, level: int | None) -> None:
        """level None or 0 removes the user's entry."""
        content = await self.state_event(room_id, "m.room.power_levels") or {}
        users = dict(content.get("users", {}))
        if level:
            if users.get(user_id) == level:
                return
            users[user_id] = level
        elif user_id in users:
            del users[user_id]
        else:
            return
        content["users"] = users
        await self.put_state(room_id, "m.room.power_levels", content)

    async def set_power_key(self, room_id: RoomID, key: str, level: int) -> None:
        """E.g. archive: raise events_default so students can't post."""
        content = await self.state_event(room_id, "m.room.power_levels") or {}
        if content.get(key) == level:
            return
        content[key] = level
        await self.put_state(room_id, "m.room.power_levels", content)
