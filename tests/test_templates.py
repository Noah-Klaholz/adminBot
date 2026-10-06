import pytest

from adminbot import templates
from adminbot.errors import ValidationError

PLAN_EXAMPLE = """
version: 1
space: {name: "{code} {title} ({semester})", topic: "..."}
defaults: {join_rule: restricted, encryption: true, history_visibility: shared}
rooms:
  - {name: "Announcements", power_levels: {events_default: 50}}
  - {name: "General"}
  - {name: "Questions"}
  - {name: "Staff", join_rule: invite, members: staff}
groups: {count: 0, name: "Group {n}"}
"""


def test_plan_example_parses():
    t = templates.parse(PLAN_EXAMPLE)
    assert [r.name for r in t.rooms] == ["Announcements", "General", "Questions", "Staff"]
    assert t.rooms[0].power_levels == {"events_default": 50}
    assert t.resolved(t.rooms[3]) == ("invite", True, "shared")
    assert t.resolved(t.rooms[1]) == ("restricted", True, "shared")


def test_parse_dump_roundtrip():
    t = templates.default_template()
    assert templates.parse(templates.dump(t)) == t


@pytest.mark.parametrize("text", [
    "version: 2\nspace: {name: x}\nrooms: [{name: a}]",
    "version: 1\nspace: {name: x}\nrooms: [{name: a}]\nfoo: 1",
    "version: 1\nspace: {name: x}\nrooms: [{name: a, join_rule: knock}]",
    "version: 1\nspace: {name: x}\nrooms: [{name: a, room_version: '9'}]",
    "version: 1\nspace: {name: x}\nrooms: [{name: a, power_levels: {users: {}}}]",
    "version: 1\nspace: {name: x}\nrooms: []",
    "version: 1\nspace: {name: x}\nrooms: [{name: a}]\ngroups: {count: 2, name: Group}",
    "version: 1\nrooms: [{name: a}]",
    "- just a list",
    "version: 1\nspace: {name: x\n",
])
def test_parse_rejects(text):
    with pytest.raises(ValidationError) as e:
        templates.parse(text)
    assert e.value.key == "err.template_invalid"


def test_staff_room_forced_invite_only():
    t = templates.parse("version: 1\nspace: {name: x}\nrooms: [{name: s, join_rule: public, members: staff}]")
    assert t.resolved(t.rooms[0])[0] == "invite"


def test_render_fills_placeholders():
    t = templates.render(templates.default_template(), "CS101", "Intro", "HS26")
    assert t.space_name == "CS101 Intro (HS26)"


class FakeOps:
    """Just enough of MatrixOps for snapshot()."""
    mxid = "@bot:x"

    def __init__(self, rooms):
        self.rooms = rooms

    async def room_settings(self, room_id):
        return self.rooms[room_id]

    async def children(self, space_id):
        return [r for r in self.rooms if r != space_id]


def settings(name, join_rule="restricted", events_default=0, tag=None):
    return {"name": name, "topic": None, "join_rule": join_rule, "encryption": True,
            "history_visibility": "shared",
            "power_levels": {"events_default": events_default, "state_default": 50, "users": {}},
            "tag": tag}


async def test_snapshot_turns_names_back_into_placeholders():
    ops = FakeOps({
        "!space": settings("CS101 Intro (HS26)"),
        "!ann": settings("Announcements", events_default=50, tag={"kind": "room", "members": "all"}),
        "!staff": settings("Staff", join_rule="invite", tag={"kind": "room", "members": "staff"}),
        "!g1": settings("Group 1", join_rule="invite", tag={"kind": "group", "group": 1}),
    })
    t = await templates.snapshot(ops, "!space", "CS101", "Intro", "HS26")
    assert t.space_name == "{code} {title} ({semester})"
    assert [(r.name, r.members, r.power_levels) for r in t.rooms] == [
        ("Announcements", "all", {"events_default": 50}), ("Staff", "staff", {})]
    assert t.groups.count == 1
    assert templates.parse(templates.dump(t)) == t
