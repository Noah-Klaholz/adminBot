"""Signup service, store details and the signup web pages."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from adminbot.db import Course, SignupToken
from adminbot.errors import ValidationError
from adminbot.signups import TokenKind, hash_token
from adminbot.web.signup import SignupWeb

from .conftest import PROF, SERVER

NEW = f"@new:{SERVER}"


def raw_token(link) -> str:
    return parse_qs(urlparse(link.link).query)["t"][0]


async def make_course(bot):
    await bot.store.create_course(Course("CS101", "Intro", "HS26", f"!space:{SERVER}", PROF, False,
                                         datetime.now(timezone.utc)))


async def test_redeem_creates_account_and_applies_invites(bot):
    await make_course(bot)
    bot.synapse.user_exists = AsyncMock(return_value=False)
    bot.ops.children = AsyncMock(return_value=[f"!general:{SERVER}", f"!staff:{SERVER}"])
    bot.ops.tag = AsyncMock(side_effect=lambda room: {"kind": "room", "members": "staff" if "staff" in room else "all"})
    first = await bot.signups.create(NEW, ["new@unibas.ch"], PROF, "CS101")
    second = await bot.signups.create(NEW, ["new@stud.unibas.ch"], PROF, None)

    user_id = await bot.signups.redeem_signup(raw_token(first), "New Person", "correct horse battery")
    assert user_id == NEW
    bot.synapse.create_user.assert_awaited_once_with(NEW, "correct horse battery", "New Person", ["new@unibas.ch"])
    joined = [call.args for call in bot.synapse.force_join.await_args_list]
    assert joined == [(NEW, f"!space:{SERVER}"), (NEW, f"!general:{SERVER}")]
    # Single use, and the other open link for the same person is gone too.
    with pytest.raises(ValidationError):
        await bot.signups.redeem_signup(raw_token(first), "x", "correct horse battery")
    assert await bot.signups.lookup(raw_token(second), TokenKind.SIGNUP) is None
    assert await bot.store.pending_invites(NEW) == []


async def test_failed_account_creation_keeps_link_usable(bot):
    bot.synapse.user_exists = AsyncMock(return_value=False)
    link = await bot.signups.create(NEW, ["new@unibas.ch"], PROF)
    bot.synapse.create_user = AsyncMock(side_effect=RuntimeError("synapse down"))
    with pytest.raises(RuntimeError):
        await bot.signups.redeem_signup(raw_token(link), "", "correct horse battery")
    assert await bot.signups.lookup(raw_token(link), TokenKind.SIGNUP) is not None


async def test_reset_link_wrong_kind_and_expiry(bot):
    bot.synapse.reset_password = AsyncMock()
    link = await bot.signups.create_reset(NEW, PROF)
    assert await bot.signups.lookup(raw_token(link), TokenKind.SIGNUP) is None
    assert await bot.signups.redeem_reset(raw_token(link), "correct horse battery") == NEW
    bot.synapse.reset_password.assert_awaited_once_with(NEW, "correct horse battery", logout_devices=True)


async def test_expire_deletes_tokens_and_orphaned_invites(bot):
    await make_course(bot)
    bot.synapse.user_exists = AsyncMock(return_value=False)
    await bot.signups.create(NEW, ["new@unibas.ch"], PROF, "CS101")
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await bot.store.add_token(SignupToken("h", "signup", f"@old:{SERVER}", ["old@unibas.ch"], None, PROF,
                                          past, past, None))
    assert await bot.signups.expire() == 1
    assert await bot.store.get_token("h") is None
    assert len(await bot.store.pending_invites(NEW)) == 1


async def test_store_rename_course(bot):
    await make_course(bot)
    await bot.store.add_staff("CS101", PROF, "owner")
    await bot.store.rename_course("CS101", "CS101-HS26")
    assert await bot.store.get_course("CS101") is None
    assert await bot.store.staff_role("CS101-HS26", PROF) == "owner"


@pytest.fixture
async def web_client(bot):
    pages = SignupWeb(bot)
    app = web.Application()
    app.router.add_get("/signup", pages.signup_form)
    app.router.add_post("/signup", pages.signup_submit)
    async with TestClient(TestServer(app)) as client:
        yield client, pages


async def test_signup_pages(bot, web_client):
    client, pages = web_client
    bot.synapse.user_exists = AsyncMock(return_value=False)
    raw = raw_token(await bot.signups.create(NEW, ["new@unibas.ch"], PROF))

    resp = await client.get(f"/signup?t={raw}")
    assert resp.status == 200 and NEW in await resp.text()
    assert resp.headers["Referrer-Policy"] == "no-referrer"

    nonce = pages.nonce_for(hash_token(raw))
    form = {"t": raw, "nonce": nonce, "displayname": "N", "password": "a" * 12, "confirm": "b" * 12}
    resp = await client.post(f"/signup?t={raw}", data=form)
    assert resp.status == 400 and "don&#x27;t match" in await resp.text()

    resp = await client.post(f"/signup?t={raw}", data={**form, "nonce": "forged", "confirm": "a" * 12})
    assert resp.status == 400 and "invalid" in await resp.text()

    resp = await client.post(f"/signup?t={raw}", data={**form, "confirm": "a" * 12})
    assert resp.status == 200 and "ready" in await resp.text()
    bot.synapse.create_user.assert_awaited_once()

    resp = await client.get(f"/signup?t={raw}")
    assert resp.status == 400


async def test_signup_page_rate_limit(bot, web_client):
    client, pages = web_client
    pages.limiter.per_ip_per_hour = 2
    statuses = [(await client.get("/signup?t=nope")).status for _ in range(3)]
    assert statuses == [400, 400, 429]
