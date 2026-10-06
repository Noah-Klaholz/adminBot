from adminbot.matrix_ops import is_dm_members
from adminbot.roster import diff_roster, round_robin

BOT, PROF, TA = "@bot:x", "@prof:x", "@ta:x"


def test_diff_never_removes_protected():
    diff = diff_roster(wanted={"@a:x"}, members={"@a:x", "@b:x", BOT, PROF, TA}, protected={BOT, PROF, TA})
    assert diff.to_remove == ["@b:x"]
    assert diff.to_add == []
    assert diff.unchanged == ["@a:x"]


def test_diff_add():
    diff = diff_roster(wanted={"@a:x", "@c:x"}, members={"@a:x"}, protected=set())
    assert diff.to_add == ["@c:x"]


def test_round_robin_is_even_and_stable():
    users = [f"@u{i}:x" for i in range(7)]
    result = round_robin(list(reversed(users)), 3)
    assert [len(v) for v in result.values()] == [3, 2, 2]
    assert result == round_robin(users, 3)


def test_is_dm_members():
    assert is_dm_members({BOT, PROF}, set(), BOT, PROF)
    assert not is_dm_members({BOT, PROF, TA}, set(), BOT, PROF)
    assert not is_dm_members({BOT, PROF}, {TA}, BOT, PROF)
    assert not is_dm_members({BOT}, {PROF}, BOT, PROF)
    assert not is_dm_members({BOT, PROF}, set(), BOT, BOT)
