"""Fixtures. maubot[testing] provides maubot_plugin / maubot_test_bot (a fake Matrix client that
records what the bot sends) and a temporary SQLite plugin DB. We point them at this plugin and
replace everything that would talk to a real server with AsyncMocks."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from mautrix.types import MessageType

from adminbot.bot import AdminBot
from adminbot.config import Config
from adminbot.migrations import upgrade_table

PLUGIN_DIR = Path(__file__).resolve().parent.parent
SERVER = "example.com"
ADMIN = "@admin:example.com"
PROF = "@prof:example.com"
TA = "@ta:example.com"
STUDENT = "@student:example.com"
DM = "!dm:example.com"
BASE_URL = "https://example.com/_matrix/maubot/plugin/adminbot"


@pytest.fixture
def maubot_plugin_path() -> Path:
    return PLUGIN_DIR


@pytest.fixture
def maubot_plugin_class() -> type:
    return AdminBot


@pytest.fixture
def maubot_plugin_config_class() -> type:
    return Config


@pytest.fixture
def maubot_upgrade_table():
    return upgrade_table


@pytest.fixture
def maubot_plugin_config_overrides() -> dict:
    return {
        "admins": [ADMIN],
        "professors": [PROF],
        "server_name": SERVER,
        "startup": {"self_check": False},
        "signup": {
            "public_base_url": BASE_URL,
            "login_url": "https://example.com",
            "token_ttl_days": 14,
            "reset_ttl_hours": 24,
            "min_password_length": 12,
            "trusted_proxies": ["127.0.0.1/32"],
            "rate_limit": {"per_ip_per_hour": 100, "global_per_hour": 1000},
        },
        "jobs": {"delay_seconds": 0},
    }


@pytest_asyncio.fixture
async def bot(maubot_plugin) -> AdminBot:
    """The started plugin with all server calls mocked. Rooms are always DMs unless a test says
    otherwise; every user exists unless a test says otherwise."""
    p: AdminBot = maubot_plugin
    p.ops.is_dm = AsyncMock(return_value=True)
    p.ops.tag = AsyncMock(return_value=None)
    p.ops.is_encrypted = AsyncMock(return_value=False)
    p.ops.children = AsyncMock(return_value=[])
    p.ops.invite = AsyncMock()
    p.ops.open_dm = AsyncMock(return_value=DM)
    p.ops.remember_dm = AsyncMock()
    p.ops.joined_or_invited = AsyncMock(return_value=set())
    p.client.upload_media = AsyncMock(return_value="mxc://example.com/file")
    p.synapse.user_exists = AsyncMock(return_value=True)
    p.synapse.create_user = AsyncMock()
    p.synapse.force_join = AsyncMock()
    yield p
    await p.jobs.stop()


class Chat:
    """Send messages as a user and read the bot's replies."""

    def __init__(self, test_bot) -> None:
        self.test_bot = test_bot

    async def send(self, text: str, sender: str = ADMIN, room_id: str = DM) -> list[str]:
        before = len(self.test_bot.responded)
        await self.test_bot.send(text, sender=sender, room_id=room_id)
        return [r.content.body for r in self.test_bot.responded[before:]]

    def files(self) -> list:
        return [r.content for r in self.test_bot.responded if getattr(r.content, "msgtype", None) == MessageType.FILE]


@pytest.fixture
def chat(bot, maubot_test_bot) -> Chat:
    return Chat(maubot_test_bot)


async def wait_for_jobs(bot: AdminBot, timeout: float = 5) -> None:
    async def done() -> bool:
        return not await bot.store.unfinished_jobs() and bot.jobs.queue.empty()

    for _ in range(int(timeout / 0.02)):
        if await done():
            await asyncio.sleep(0.02)
            return
        await asyncio.sleep(0.02)
    raise AssertionError("jobs did not finish")
