"""Course layout templates (plan §9): YAML schema, export from a live course, apply.

parse/dump/render/default_template are pure; snapshot/apply talk to Matrix via MatrixOps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import io
from typing import Any

from mautrix.types import RoomID, UserID
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .errors import ValidationError
from .matrix_ops import BASE_POWER, MatrixOps

SCHEMA_VERSION = 1
JOIN_RULES = ("restricted", "invite", "public")
HISTORY = ("shared", "invited", "joined")
MEMBERS = ("all", "staff")
PL_KEYS = tuple(BASE_POWER)
MAX_ROOMS = 50
MAX_GROUPS = 50
DEFAULTS = {"join_rule": "restricted", "encryption": True, "history_visibility": "shared"}
TOP_KEYS = {"version", "space", "defaults", "rooms", "groups"}
ROOM_KEYS = {"name", "topic", "join_rule", "encryption", "history_visibility", "power_levels", "members"}


@dataclass
class RoomSpec:
    name: str
    topic: str | None = None
    join_rule: str | None = None  # None = template default
    encryption: bool | None = None
    history_visibility: str | None = None
    power_levels: dict[str, int] = field(default_factory=dict)  # overrides, e.g. events_default
    members: str = "all"  # "all" | "staff"


@dataclass
class GroupSpec:
    count: int = 0
    name: str = "Group {n}"


@dataclass
class Template:
    space_name: str  # placeholders: {code} {title} {semester}
    space_topic: str = ""
    defaults: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    rooms: list[RoomSpec] = field(default_factory=list)
    groups: GroupSpec = field(default_factory=GroupSpec)

    def resolved(self, spec: RoomSpec) -> tuple[str, bool, str]:
        """(join_rule, encryption, history_visibility) of a room after applying defaults.
        Staff-only rooms are always invite-only."""
        join_rule = spec.join_rule or self.defaults.get("join_rule", DEFAULTS["join_rule"])
        if spec.members == "staff":
            join_rule = "invite"
        encryption = spec.encryption if spec.encryption is not None else self.defaults.get(
            "encryption", DEFAULTS["encryption"])
        history = spec.history_visibility or self.defaults.get("history_visibility", DEFAULTS["history_visibility"])
        return join_rule, bool(encryption), history


def _fail(detail: str) -> ValidationError:
    return ValidationError("err.template_invalid", detail=detail)


def _check_choice(value: Any, choices: tuple[str, ...], where: str) -> str:
    if value not in choices:
        raise _fail(f"{where}: '{value}' (allowed: {', '.join(choices)})")
    return value


def _check_power(value: Any, where: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise _fail(f"{where}: power_levels must be a mapping")
    result = {}
    for key, level in value.items():
        if key not in PL_KEYS:
            raise _fail(f"{where}: unknown power level '{key}' (allowed: {', '.join(PL_KEYS)})")
        if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 100:
            raise _fail(f"{where}: power level '{key}' must be 0-100")
        result[str(key)] = level
    return result


def parse(text: str) -> Template:
    """Load YAML (safe) and validate. Raises ValidationError with a readable detail."""
    try:
        data = YAML(typ="safe").load(text)
    except YAMLError as e:
        raise _fail(str(e).splitlines()[0]) from e
    if not isinstance(data, dict):
        raise _fail("not a mapping")
    unknown = set(data) - TOP_KEYS
    if unknown:
        raise _fail(f"unknown keys: {', '.join(sorted(map(str, unknown)))}")
    if data.get("version") != SCHEMA_VERSION:
        raise _fail(f"version must be {SCHEMA_VERSION}")

    space = data.get("space") or {}
    if not isinstance(space, dict) or not isinstance(space.get("name"), str) or not space["name"].strip():
        raise _fail("space.name is required")

    defaults = dict(DEFAULTS)
    raw_defaults = data.get("defaults") or {}
    if not isinstance(raw_defaults, dict):
        raise _fail("defaults must be a mapping")
    for key, value in raw_defaults.items():
        if key == "join_rule":
            defaults[key] = _check_choice(value, JOIN_RULES, "defaults.join_rule")
        elif key == "history_visibility":
            defaults[key] = _check_choice(value, HISTORY, "defaults.history_visibility")
        elif key == "encryption":
            if not isinstance(value, bool):
                raise _fail("defaults.encryption must be true or false")
            defaults[key] = value
        else:
            raise _fail(f"unknown key defaults.{key}")

    rooms_data = data.get("rooms") or []
    if not isinstance(rooms_data, list) or not rooms_data:
        raise _fail("rooms must be a non-empty list")
    if len(rooms_data) > MAX_ROOMS:
        raise _fail(f"at most {MAX_ROOMS} rooms")
    rooms = []
    for i, room in enumerate(rooms_data, start=1):
        where = f"rooms[{i}]"
        if not isinstance(room, dict) or not isinstance(room.get("name"), str) or not room["name"].strip():
            raise _fail(f"{where}: name is required")
        unknown = set(room) - ROOM_KEYS
        if unknown:
            raise _fail(f"{where}: unknown keys: {', '.join(sorted(map(str, unknown)))}")
        encryption = room.get("encryption")
        if encryption is not None and not isinstance(encryption, bool):
            raise _fail(f"{where}: encryption must be true or false")
        rooms.append(RoomSpec(
            name=room["name"].strip(),
            topic=str(room["topic"]) if room.get("topic") else None,
            join_rule=_check_choice(room["join_rule"], JOIN_RULES, where) if room.get("join_rule") else None,
            encryption=encryption,
            history_visibility=(_check_choice(room["history_visibility"], HISTORY, where)
                                if room.get("history_visibility") else None),
            power_levels=_check_power(room.get("power_levels") or {}, where),
            members=_check_choice(room.get("members", "all"), MEMBERS, where),
        ))

    groups_data = data.get("groups") or {}
    if not isinstance(groups_data, dict):
        raise _fail("groups must be a mapping")
    count = groups_data.get("count", 0)
    if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= MAX_GROUPS:
        raise _fail(f"groups.count must be 0-{MAX_GROUPS}")
    group_name = groups_data.get("name", "Group {n}")
    if not isinstance(group_name, str) or "{n}" not in group_name:
        raise _fail("groups.name must contain {n}")

    return Template(
        space_name=space["name"].strip(),
        space_topic=str(space.get("topic") or ""),
        defaults=defaults,
        rooms=rooms,
        groups=GroupSpec(count=count, name=group_name),
    )


def to_dict(template: Template) -> dict[str, Any]:
    rooms = []
    for spec in template.rooms:
        room: dict[str, Any] = {"name": spec.name}
        if spec.topic:
            room["topic"] = spec.topic
        if spec.join_rule:
            room["join_rule"] = spec.join_rule
        if spec.encryption is not None:
            room["encryption"] = spec.encryption
        if spec.history_visibility:
            room["history_visibility"] = spec.history_visibility
        if spec.power_levels:
            room["power_levels"] = dict(spec.power_levels)
        if spec.members != "all":
            room["members"] = spec.members
        rooms.append(room)
    space: dict[str, Any] = {"name": template.space_name}
    if template.space_topic:
        space["topic"] = template.space_topic
    return {
        "version": SCHEMA_VERSION,
        "space": space,
        "defaults": dict(template.defaults),
        "rooms": rooms,
        "groups": {"count": template.groups.count, "name": template.groups.name},
    }


def dump(template: Template) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    buf = io.StringIO()
    yaml.dump(to_dict(template), buf)
    return buf.getvalue()


def _fill(text: str, code: str, title: str, semester: str) -> str:
    return text.replace("{code}", code).replace("{title}", title).replace("{semester}", semester)


def render(template: Template, code: str, title: str, semester: str) -> Template:
    """Copy with placeholders filled in (room names and topics may use them too)."""
    return Template(
        space_name=_fill(template.space_name, code, title, semester),
        space_topic=_fill(template.space_topic, code, title, semester),
        defaults=dict(template.defaults),
        rooms=[
            RoomSpec(
                name=_fill(spec.name, code, title, semester),
                topic=_fill(spec.topic, code, title, semester) if spec.topic else None,
                join_rule=spec.join_rule,
                encryption=spec.encryption,
                history_visibility=spec.history_visibility,
                power_levels=dict(spec.power_levels),
                members=spec.members,
            )
            for spec in template.rooms
        ],
        groups=GroupSpec(template.groups.count, template.groups.name),
    )


def default_template() -> Template:
    """Used when --template is not given."""
    return Template(
        space_name="{code} {title} ({semester})",
        rooms=[
            RoomSpec(name="Announcements", power_levels={"events_default": 50}),
            RoomSpec(name="General"),
            RoomSpec(name="Questions"),
            RoomSpec(name="Staff", members="staff"),
        ],
    )


def _unfill(text: str, code: str, title: str, semester: str) -> str:
    for value, placeholder in ((title, "{title}"), (code, "{code}"), (semester, "{semester}")):
        if value:
            text = text.replace(value, placeholder)
    return text


async def snapshot(ops: MatrixOps, space_id: RoomID, code: str, title: str, semester: str) -> Template:
    """Read the live course: child order, names, topics, join rules, encryption, history,
    power-level overrides and staff-only flags. Members are left out. Course-specific names
    are turned back into placeholders."""
    space = await ops.room_settings(space_id)
    rooms = []
    group_count = 0
    for room_id in await ops.children(space_id):
        settings = await ops.room_settings(room_id)
        tag = settings["tag"] or {}
        if tag.get("kind") == "group":
            group_count += 1
            continue
        members = tag.get("members", "all") if tag.get("members") in MEMBERS else "all"
        pl = settings["power_levels"]
        overrides = {k: pl[k] for k in PL_KEYS if k in pl and pl[k] != BASE_POWER[k]}
        join_rule = settings["join_rule"] if settings["join_rule"] in JOIN_RULES else "invite"
        history = settings["history_visibility"] if settings["history_visibility"] in HISTORY else "shared"
        rooms.append(RoomSpec(
            name=_unfill(settings["name"] or "Room", code, title, semester),
            topic=_unfill(settings["topic"], code, title, semester) if settings["topic"] else None,
            join_rule=None if members == "staff" or join_rule == DEFAULTS["join_rule"] else join_rule,
            encryption=None if settings["encryption"] == DEFAULTS["encryption"] else settings["encryption"],
            history_visibility=None if history == DEFAULTS["history_visibility"] else history,
            power_levels=overrides,
            members=members,
        ))
    if not rooms:
        raise ValidationError("err.template_empty_course")
    return Template(
        space_name=_unfill(space["name"] or "{code}", code, title, semester),
        space_topic=_unfill(space["topic"] or "", code, title, semester),
        defaults=dict(DEFAULTS),
        rooms=rooms,
        groups=GroupSpec(count=group_count),
    )


@dataclass
class AppliedCourse:
    space_id: RoomID
    rooms: list[tuple[str, RoomID]]


def staff_power(owner: UserID, tas: list[UserID], levels: dict[str, int], bot: UserID) -> dict[UserID, int]:
    users = {ta: levels["ta"] for ta in tas}
    users[owner] = levels["owner"]
    users[bot] = 100
    return users


async def create_spec_room(ops: MatrixOps, template: Template, spec: RoomSpec, space_id: RoomID,
                           code: str, users: dict[UserID, int], staff: list[UserID],
                           order: str) -> RoomID:
    join_rule, encryption, history = template.resolved(spec)
    room_id = await ops.create_room(
        spec.name, spec.topic, space_id=space_id, join_rule=join_rule, encryption=encryption,
        history_visibility=history, power_users=users, power_overrides=spec.power_levels,
        invitees=staff, tag={"kind": "room", "course": code, "members": spec.members},
    )
    await ops.add_child(space_id, room_id, order=order)
    return room_id


async def create_group_room(ops: MatrixOps, space_id: RoomID, code: str, number: int, name_format: str,
                            users: dict[UserID, int], staff: list[UserID], encryption: bool) -> RoomID:
    room_id = await ops.create_room(
        name_format.replace("{n}", str(number)), None, space_id=space_id, join_rule="invite",
        encryption=encryption, history_visibility="shared", power_users=users, power_overrides={},
        invitees=staff, tag={"kind": "group", "course": code, "group": number, "members": "staff"},
    )
    await ops.add_child(space_id, room_id, order=f"g{number:03d}", suggested=False)
    return room_id


async def apply(ops: MatrixOps, template: Template, code: str, owner: UserID, tas: list[UserID],
                levels: dict[str, int]) -> AppliedCourse:
    """Create the space and all rooms (restricted to the space where requested), owner at
    levels['owner'], TAs at levels['ta']. Staff are invited everywhere. `template` must be rendered."""
    users = staff_power(owner, tas, levels, ops.mxid)
    staff = [owner, *tas]
    space_id = await ops.create_space(
        template.space_name, template.space_topic, users, staff, tag={"kind": "space", "course": code},
    )
    rooms = []
    for i, spec in enumerate(template.rooms):
        room_id = await create_spec_room(ops, template, spec, space_id, code, users, staff, f"{i:03d}")
        rooms.append((spec.name, room_id))
    encryption = bool(template.defaults.get("encryption", True))
    for n in range(1, template.groups.count + 1):
        room_id = await create_group_room(ops, space_id, code, n, template.groups.name, users, staff, encryption)
        rooms.append((template.groups.name.replace("{n}", str(n)), room_id))
    return AppliedCourse(space_id, rooms)
