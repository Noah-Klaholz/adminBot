"""Command routing and flows against maubot's fake client (no server needed)."""
from datetime import datetime, timezone
import itertools
import re
from unittest.mock import AsyncMock

from adminbot.db import Course

from .conftest import ADMIN, DM, PROF, SERVER, STUDENT, TA, wait_for_jobs


async def make_course(bot, code="CS101", owner=PROF):
    await bot.store.create_course(Course(code, "Intro", "HS26", f"!{code.lower()}:{SERVER}", owner, False,
                                         datetime.now(timezone.utc)))
    await bot.store.add_staff(code, owner, "owner")


async def test_unknown_sender_gets_no_reply(chat):
    assert await chat.send("!help", sender=STUDENT) == []
    assert await chat.send("!course create X y", sender=STUDENT) == []


async def test_foreign_server_and_unrelated_messages_ignored(chat):
    assert await chat.send("!help", sender="@admin:evil.org") == []
    assert await chat.send("hello", sender=ADMIN) == []
    assert await chat.send("!unknowncommand", sender=ADMIN) == []


async def test_command_outside_dm_is_refused(bot, chat):
    bot.ops.is_dm = AsyncMock(return_value=False)
    replies = await chat.send("!help", sender=ADMIN)
    assert replies == ["Please send me commands in a direct chat with only you and me."]


async def test_help_depends_on_role(chat):
    admin_help = (await chat.send("!help", sender=ADMIN))[0]
    prof_help = (await chat.send("!help", sender=PROF))[0]
    assert "!admin prof" in admin_help and "!course create" in admin_help
    assert "!admin prof" not in prof_help and "!course create" in prof_help


async def test_missing_subcommand_lists_usages(chat):
    reply = (await chat.send("!course", sender=PROF))[0]
    assert "!course create" in reply and "!course archive" in reply


async def test_whoami(bot, chat):
    await make_course(bot)
    reply = (await chat.send("!whoami", sender=PROF))[0]
    assert "professor" in reply and "CS101 (owner)" in reply


async def test_admin_prof_add_only_for_admins(bot, chat):
    assert "not allowed" in (await chat.send(f"!admin prof add {TA}", sender=PROF))[0]
    assert "is now a professor" in (await chat.send(f"!admin prof add {TA}", sender=ADMIN))[0]
    assert await bot.store.is_professor(TA)
    entries = await bot.store.recent_audit(5)
    assert entries[0].action == "professor.add" and entries[0].actor == ADMIN


async def test_admin_prof_add_unknown_user(bot, chat):
    bot.synapse.user_exists = AsyncMock(return_value=False)
    assert "has no account" in (await chat.send(f"!admin prof add {TA}", sender=ADMIN))[0]


async def test_course_create(bot, chat):
    ids = (f"!room{i}:{SERVER}" for i in itertools.count())
    bot.ops.create_space = AsyncMock(return_value=f"!space:{SERVER}")
    bot.ops.create_room = AsyncMock(side_effect=lambda *a, **k: next(ids))
    bot.ops.add_child = AsyncMock()
    replies = await chat.send('!course create cs101 "Intro to CS" --semester hs26', sender=PROF)
    assert "Course **CS101** is ready" in replies[-1]
    course = await bot.store.get_course("CS101")
    assert (course.title, course.semester, course.owner) == ("Intro to CS", "HS26", PROF)
    assert await bot.store.staff_role("CS101", PROF) == "owner"
    assert bot.ops.create_room.await_count == 4
    staff_call = bot.ops.create_room.await_args_list[3]
    assert staff_call.kwargs["join_rule"] == "invite" and staff_call.kwargs["tag"]["members"] == "staff"
    assert bot.ops.create_space.await_args.args[0] == "CS101 Intro to CS (HS26)"
    assert "already taken" in (await chat.send('!course create CS101 "x"', sender=PROF))[-1]
    assert "**CS101** Intro to CS (HS26), owner" in (await chat.send("!course list", sender=PROF))[0]


async def test_course_create_rejects_bad_input(chat):
    assert "not a semester" in (await chat.send('!course create CS1 "T" --semester 2026', sender=PROF))[0]
    assert "not a valid course code" in (await chat.send('!course create "C S" "T"', sender=PROF))[0]
    assert "Unknown option" in (await chat.send('!course create CS1 "T" --foo', sender=PROF))[0]


async def test_ta_scoping(bot, chat):
    await make_course(bot, "CS101")
    await make_course(bot, "CS102")
    await bot.store.add_staff("CS101", TA, "ta")
    bot.ops.room_settings = AsyncMock(return_value={"name": "Room"})
    assert "**CS101**" in (await chat.send("!course info CS101", sender=TA))[0]
    assert "not allowed" in (await chat.send("!course info CS102", sender=TA))[0]
    assert "not allowed" in (await chat.send("!course archive CS101", sender=TA))[0]
    assert "There is no course" in (await chat.send("!course info NOPE", sender=TA))[0]


async def test_invite_flow(bot, chat, maubot_test_bot):
    await make_course(bot)
    bot.synapse.user_exists = AsyncMock(side_effect=lambda uid: uid == f"@old:{SERVER}")
    text = "!invite CS101\nold@unibas.ch, New@stud.unibas.ch new@unibas.ch\nbad@gmail.com"
    plan = (await chat.send(text, sender=PROF))[0]
    assert "1 existing accounts" in plan and "1 new people" in plan and "1 entries are not usable" in plan
    code = re.search(r"!confirm (\w+)", plan).group(1)

    assert "No pending plan" in (await chat.send(f"!confirm {code}", sender=ADMIN))[0]
    await chat.send(f"!confirm {code}", sender=PROF)
    await wait_for_jobs(bot)

    bot.ops.invite.assert_awaited_with(f"!cs101:{SERVER}", f"@old:{SERVER}")
    tokens = await bot.signups.open_tokens(f"@new:{SERVER}")
    assert len(tokens) == 1 and tokens[0].emails == ["new@stud.unibas.ch", "new@unibas.ch"]
    assert [i.course for i in await bot.store.pending_invites(f"@new:{SERVER}")] == ["CS101"]
    assert [f.body for f in chat.files()] == ["signup-links.csv"]
    job = (await bot.store.recent_audit(1))[0]
    assert job.action == "invite" and job.target_count == 1
    stored = await bot.store.get_job(job.job_id)
    assert "new" not in stored.context  # emails dropped from the job


async def test_dry_run_and_cancel(bot, chat):
    await make_course(bot)
    reply = (await chat.send("!invite CS101 --dry-run a@unibas.ch", sender=PROF))[0]
    assert "Dry run" in reply
    reply = (await chat.send("!invite CS101 a@unibas.ch", sender=PROF))[0]
    assert "!confirm" in reply
    assert (await chat.send("!cancel", sender=PROF))[0] == "Cancelled."
    code = re.search(r"!confirm (\w+)", reply).group(1)
    assert "No pending plan" in (await chat.send(f"!confirm {code}", sender=PROF))[0]


async def test_invite_waits_for_file(bot, chat):
    await make_course(bot)
    reply = (await chat.send("!invite CS101", sender=PROF))[0]
    assert "Send the file now" in reply
    assert bot.uploads.is_waiting(DM, PROF)


async def test_signup_new_and_revoke(bot, chat):
    bot.synapse.user_exists = AsyncMock(return_value=False)
    reply = (await chat.send("!signup new Anna.Beispiel@stud.unibas.ch", sender=PROF))[0]
    assert f"@anna.beispiel:{SERVER}" in reply and "/signup?t=" in reply
    assert "1 open link" in (await chat.send("!signup revoke anna.beispiel@unibas.ch", sender=PROF))[0]
    assert "has no open signup link" in (await chat.send(f"!signup revoke @anna.beispiel:{SERVER}",
                                                         sender=PROF))[0]


async def test_ta_cannot_create_links_without_course_and_reset_is_admin_only(bot, chat):
    await make_course(bot)
    await bot.store.add_staff("CS101", TA, "ta")
    assert "not allowed" in (await chat.send("!signup new a@unibas.ch", sender=TA))[0]
    assert "not allowed" in (await chat.send(f"!signup reset {STUDENT}", sender=PROF))[0]
    assert "Password-reset link" in (await chat.send(f"!signup reset {STUDENT}", sender=ADMIN))[0]


async def test_template_list_and_unknown(bot, chat):
    await bot.store.save_template("base", "global", None, "version: 1")
    await bot.store.save_template("mine", "personal", PROF, "version: 1")
    await bot.store.save_template("other", "personal", ADMIN, "version: 1")
    reply = (await chat.send("!template list", sender=PROF))[0]
    assert "base" in reply and "mine" in reply and "other" not in reply
    assert "no template" in (await chat.send("!template export --saved nope", sender=PROF))[0]


async def test_bots_disabled(chat):
    reply = (await chat.send("!bot list", sender=PROF))[0]
    assert "startup check failed" in reply or "not enabled" in reply
