"""Plugin entry point: builds the services, runs the self-check and registers the handlers."""
from __future__ import annotations

from typing import Type

from maubot import Plugin
from mautrix.util.async_db import UpgradeTable
from mautrix.util.config import BaseProxyConfig

from . import strings
from .attachments import PendingUploads
from .audit import Audit
from .authz import Authz
from .commands import COMMAND_CLASSES, CommandRouter
from .config import Config
from .confirm import ConfirmRegistry
from .db import Store
from .jobs import JobRunner
from .matrix_ops import MatrixOps
from .migrations import upgrade_table
from .signups import SignupService
from .synapse_admin import SynapseAdmin
from .userbots import BotManager, UserbotsClient
from .web import WEB_CLASSES

CLEANUP_INTERVAL = 300


class AdminBot(Plugin):
    config: Config
    store: Store
    synapse: SynapseAdmin
    ops: MatrixOps
    authz: Authz
    audit: Audit
    confirm: ConfirmRegistry
    jobs: JobRunner
    signups: SignupService
    uploads: PendingUploads
    bots: BotManager | None
    router: CommandRouter
    # Names of startup checks that failed ("admin", "audit_room", "userbots", ...).
    failed_checks: set[str]

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    @classmethod
    def get_db_upgrade_table(cls) -> UpgradeTable | None:
        return upgrade_table

    @property
    def server_name(self) -> str:
        return self.config["server_name"] or self.client.mxid.split(":", 1)[1]

    async def start(self) -> None:
        self.config.load_and_update()
        self.failed_checks = set()
        self.store = Store(self.database)
        self.synapse = SynapseAdmin(self.client, self.http)
        self._build_services()
        for professor in self.config["professors"] or []:
            await self.store.add_professor(professor, "config")
        if self.config["startup.self_check"]:
            await self.self_check()
        self.router = CommandRouter(self, [cls(self) for cls in COMMAND_CLASSES])
        self.register_handler_class(self.router)
        for cls in WEB_CLASSES:
            self.register_handler_class(cls(self))
        await self.jobs.start()
        self.sched.run_periodically(CLEANUP_INTERVAL, self.cleanup)

    def _build_services(self) -> None:
        """Everything that only depends on config values (rebuilt when the config changes)."""
        strings.set_language(self.config["language"])
        self.ops = MatrixOps(self.client, self.server_name)
        self.authz = Authz(self.store, self.config)
        self.audit = Audit(self.store, self.ops, self.config["audit_room"])
        self.confirm = ConfirmRegistry(self.store, self.config["confirm.ttl_minutes"])
        self.signups = SignupService(self.store, self.synapse, self.ops, self.audit, self.config)
        self.uploads = PendingUploads(self.config["uploads.wait_minutes"])
        if getattr(self, "jobs", None) is None:
            self.jobs = JobRunner(self, self.config["jobs.delay_seconds"])
        else:
            self.jobs.delay_seconds = self.config["jobs.delay_seconds"]
        self.bots = None
        if self.config["user_bots.enabled"]:
            self.bots = BotManager(self, UserbotsClient(
                self.http, self.config["user_bots.maubot_url"], self.config["user_bots.username"],
                self.config["user_bots.password"],
            ))

    async def stop(self) -> None:
        if getattr(self, "jobs", None):
            await self.jobs.stop()

    async def on_external_config_update(self) -> None:
        """Instance config edited in the maubot UI. Handler classes keep a reference to `self`,
        so rebuilding the services here is enough; the web handlers (rate limits) need a reload."""
        self.config.load_and_update()
        self._build_services()
        if self.config["startup.self_check"]:
            await self.self_check()

    async def self_check(self) -> None:
        """Plan §2. Fills self.failed_checks and reports to the audit room."""
        failed: set[str] = set()
        mxid = self.client.mxid
        report = []

        async def check(name: str, ok: bool, detail: str = "", fatal: bool = True) -> None:
            if not ok and fatal:
                failed.add(name)
            report.append((name, ok, detail))
            if not ok:
                self.log.warning(f"Startup check {name} failed: {detail}")

        try:
            is_admin = await self.synapse.is_server_admin(mxid)
            await check("admin", is_admin, "" if is_admin else "the bot is not a Synapse server admin")
        except Exception as e:
            is_admin = False
            await check("admin", False, str(e))
        if is_admin:
            try:
                await self.synapse.override_ratelimit(mxid)
                await check("ratelimit", True)
            except Exception as e:
                await check("ratelimit", False, str(e), fatal=False)
        room = self.config["audit_room"]
        if not room:
            await check("audit_room", False, "audit_room is not configured", fatal=False)
        else:
            try:
                joined = mxid in await self.ops.joined_members(room)
                await check("audit_room", joined, "" if joined else "the bot is not in the audit room", fatal=False)
            except Exception as e:
                await check("audit_room", False, str(e), fatal=False)
        postgres = await self.store.is_postgres()
        await check("postgres", postgres, "" if postgres else "plugin data is in SQLite, not in the backup",
                    fatal=False)
        if self.bots is not None:
            try:
                separate = await self.bots.client.is_separate_from(mxid)
                await check("userbots", separate,
                            "" if separate else "user_bots.maubot_url points at THIS maubot - refusing")
            except Exception as e:
                await check("userbots", False, str(e))
        else:
            failed.add("userbots")
        self.failed_checks = failed
        for name, ok, detail in report:
            if not ok or name == "admin":
                await self.audit.report_check(name, ok, detail)

    async def cleanup(self) -> None:
        """Expire plans, signup/reset tokens (deleting their emails) and waiting uploads."""
        await self.confirm.expire()
        await self.signups.expire()
        self.uploads.expire()
