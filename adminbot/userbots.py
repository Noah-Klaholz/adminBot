"""Professor-owned bots (plan §10): .mbp validation, client for the SECOND maubot, provisioning.

Never point user_bots.maubot_url at the maubot this plugin runs in: any plugin uploaded there
could read the admin bot's server-admin token. is_separate_from() guards against that.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import logging
import re
import secrets
from typing import TYPE_CHECKING, Any
import zipfile

import aiohttp
from mautrix.types import UserID
from packaging.requirements import InvalidRequirement, Requirement
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .confirm import Plan
from .db import BotRequest, Job, JobItem, UserBot
from .errors import Forbidden, NotFound, ValidationError
from .strings import t

if TYPE_CHECKING:
    from .bot import AdminBot

log = logging.getLogger("maubot.adminbot.userbots")

PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
BOT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
OWN_PLUGIN_ID = "ch.unibas.dmi.marvin.adminbot"


@dataclass
class MbpInfo:
    plugin_id: str
    version: str
    modules: list[str]
    main_class: str
    dependencies: list[str]
    sha256: str
    size: int


def _invalid(detail: str) -> ValidationError:
    return ValidationError("err.mbp_invalid", detail=detail)


def validate_mbp(data: bytes, max_size: int) -> MbpInfo:
    """stdlib zipfile only: size limit, maubot.yaml present and parseable, id/version,
    main module exists, no absolute or '..' paths. Raises errors.ValidationError."""
    if len(data) > max_size:
        raise ValidationError("err.file_too_large", mb=max_size // (1024 * 1024))
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise _invalid("not a zip file") from e
    infos = archive.infolist()
    names = set()
    for info in infos:
        name = info.filename
        if name.startswith("/") or "\\" in name or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name):
            raise _invalid(f"unsafe path '{name}'")
        names.add(name)
    if sum(info.file_size for info in infos) > max_size * 10:
        raise _invalid("unpacked size too large")
    if "maubot.yaml" not in names:
        raise _invalid("maubot.yaml missing")
    try:
        meta = YAML(typ="safe").load(archive.read("maubot.yaml"))
    except YAMLError as e:
        raise _invalid("maubot.yaml is not valid YAML") from e
    if not isinstance(meta, dict):
        raise _invalid("maubot.yaml is not a mapping")
    plugin_id = str(meta.get("id") or "")
    if not PLUGIN_ID_RE.match(plugin_id):
        raise _invalid("missing or invalid id")
    version = str(meta.get("version") or "")
    if not version:
        raise _invalid("missing version")
    modules = meta.get("modules") or []
    if not isinstance(modules, list) or not modules or not all(isinstance(m, str) for m in modules):
        raise _invalid("missing modules")
    main_class = str(meta.get("main_class") or "")
    if not main_class:
        raise _invalid("missing main_class")
    main_module = main_class.split("/", 1)[0] if "/" in main_class else modules[-1]
    main_path = main_module.replace(".", "/")
    if f"{main_path}/__init__.py" not in names and f"{main_path}.py" not in names:
        raise _invalid(f"main module '{main_module}' not found")
    dependencies = meta.get("dependencies") or []
    if not isinstance(dependencies, list):
        raise _invalid("dependencies must be a list")
    return MbpInfo(
        plugin_id=plugin_id, version=version, modules=modules, main_class=main_class,
        dependencies=[str(d) for d in dependencies], sha256=hashlib.sha256(data).hexdigest(), size=len(data),
    )


def missing_dependencies(dependencies: list[str]) -> list[str]:
    """Dependencies not installed in this maubot image. The userbots maubot runs the same pinned
    image, so what is missing here is missing there too."""
    missing = []
    for dep in dependencies:
        try:
            name = Requirement(dep).name
        except InvalidRequirement:
            missing.append(dep)
            continue
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(dep)
    return missing


class UserbotsError(ValidationError):
    pass


class UserbotsClient:
    """maubot management API (<maubot_url>/_matrix/maubot/v1/...) of the userbots maubot."""

    def __init__(self, http: aiohttp.ClientSession, base_url: str, username: str, password: str) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/") + "/_matrix/maubot/v1"
        self.username = username
        self.password = password
        self.token: str | None = None

    async def login(self) -> None:
        async with self.http.post(f"{self.base_url}/auth/login",
                                  json={"username": self.username, "password": self.password}) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or not isinstance(data, dict) or "token" not in data:
                raise UserbotsError("err.userbots_api", status=resp.status, detail="login failed")
            self.token = data["token"]

    async def request(self, method: str, path: str, *, allow_404: bool = False, retry: bool = True,
                      **kwargs: Any) -> Any:
        """Authenticated request; logs in again once on 401. Returns parsed JSON (or None)."""
        if self.token is None:
            await self.login()
        headers = {"Authorization": f"Bearer {self.token}", **kwargs.pop("headers", {})}
        async with self.http.request(method, f"{self.base_url}{path}", headers=headers, **kwargs) as resp:
            if resp.status == 401 and retry:
                self.token = None
                return await self.request(method, path, allow_404=allow_404, retry=False, **kwargs)
            if resp.status == 404 and allow_404:
                return None
            text = await resp.text()
            if resp.status >= 400:
                raise UserbotsError("err.userbots_api", status=resp.status, detail=text[:200])
            if not text:
                return None
            try:
                return await resp.json(content_type=None)
            except ValueError:
                return text

    async def list_clients(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/clients") or []

    async def get_client(self, user_id: UserID) -> dict[str, Any] | None:
        return await self.request("GET", f"/client/{user_id}", allow_404=True)

    async def list_instances(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/instances") or []

    async def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        return await self.request("GET", f"/instance/{instance_id}", allow_404=True)

    async def get_plugin(self, plugin_id: str) -> dict[str, Any] | None:
        return await self.request("GET", f"/plugin/{plugin_id}", allow_404=True)

    async def upload_plugin(self, data: bytes) -> dict[str, Any]:
        return await self.request(
            "POST", "/plugins/upload", params={"allow_override": "true"}, data=data,
            headers={"Content-Type": "application/zip"},
        )

    async def delete_plugin(self, plugin_id: str) -> None:
        await self.request("DELETE", f"/plugin/{plugin_id}", allow_404=True)

    async def save_client(self, user_id: UserID, homeserver: str, access_token: str, device_id: str,
                          displayname: str) -> None:
        """Create the client, or update it if a previous (interrupted) run created it already."""
        body = {
            "homeserver": homeserver, "access_token": access_token, "device_id": device_id,
            "enabled": True, "started": True, "sync": True, "autojoin": True, "displayname": displayname,
        }
        if await self.get_client(user_id):
            await self.request("PUT", f"/client/{user_id}", json=body)
        else:
            await self.request("POST", "/client/new", json=body)

    async def delete_client(self, user_id: UserID) -> None:
        await self.request("DELETE", f"/client/{user_id}", allow_404=True)

    async def put_instance(self, instance_id: str, **fields: Any) -> dict[str, Any]:
        """Create (needs type + primary_user) or partially update an instance."""
        return await self.request("PUT", f"/instance/{instance_id}", json=fields)

    async def delete_instance(self, instance_id: str) -> None:
        await self.request("DELETE", f"/instance/{instance_id}", allow_404=True)

    async def stop_all(self) -> int:
        """Kill switch: started=false on every instance."""
        count = 0
        for instance in await self.list_instances():
            if instance.get("started"):
                await self.put_instance(instance["id"], started=False)
                count += 1
        return count

    async def is_separate_from(self, own_mxid: UserID) -> bool:
        """False if our own client shows up in the target maubot (= same maubot)."""
        return all(client.get("id") != own_mxid for client in await self.list_clients())


class BotManager:
    """Requests, approval, provisioning and deletion on top of UserbotsClient."""

    def __init__(self, bot: AdminBot, client: UserbotsClient) -> None:
        self.bot = bot
        self.client = client

    @property
    def cfg(self) -> Any:
        return self.bot.config

    @staticmethod
    def check_name(name: str) -> str:
        name = (name or "").lower()
        if not BOT_NAME_RE.match(name):
            raise ValidationError("err.bot_name")
        return name

    def bot_user_id(self, owner: UserID, name: str) -> UserID:
        """@bot.<owner-localpart>.<name>:<server>"""
        return UserID(f"@bot.{owner[1:].split(':', 1)[0]}.{name}:{self.bot.server_name}")

    @staticmethod
    def instance_id(owner: UserID, name: str) -> str:
        return re.sub(r"[^a-z0-9-]", "-", f"{owner[1:].split(':', 1)[0]}-{name}")

    async def request(self, owner: UserID, name: str, data: bytes, action: str, requester: UserID) -> BotRequest:
        """Quota, package checks, plugin-ID ownership; store the request and post it to the audit
        room. Without require_approval (or when an admin uploads) it is approved right away."""
        name = self.check_name(name)
        existing = await self.bot.store.get_user_bot(owner, name)
        if action == "create":
            if existing:
                raise ValidationError("err.bot_exists", name=name)
            limit = self.cfg["user_bots.max_per_professor"]
            if not self.bot.authz.is_admin(owner) and await self.bot.store.count_user_bots(owner) >= limit:
                raise ValidationError("err.bot_quota", limit=limit)
        elif not existing:
            raise NotFound("err.bot_unknown", name=name)
        info = validate_mbp(data, self.cfg["user_bots.max_mbp_size_mb"] * 1024 * 1024)
        if info.plugin_id == OWN_PLUGIN_ID:
            raise ValidationError("err.mbp_invalid", detail="reserved plugin ID")
        plugin_owner = await self.bot.store.plugin_owner(info.plugin_id)
        if plugin_owner and plugin_owner != owner:
            raise Forbidden("err.plugin_taken", plugin_id=info.plugin_id)
        if existing and existing.sha256 == info.sha256:
            raise ValidationError("err.bot_unchanged", name=name)
        missing = missing_dependencies(info.dependencies)
        if missing:
            raise ValidationError("err.mbp_dependencies", deps=", ".join(missing))
        request = BotRequest(
            id=None, owner=owner, name=name, action=action, plugin_id=info.plugin_id, version=info.version,
            sha256=info.sha256, mbp=data, state="pending",
        )
        request.id = await self.bot.store.add_bot_request(request)
        await self.bot.audit.log(requester, f"bot.request.{action}", bot=name, target_count=1)
        auto = not self.cfg["user_bots.require_approval"] or self.bot.authz.is_admin(requester)
        if auto:
            await self.approve(request.id, requester)
            request.state = "approved"
        else:
            await self.bot.audit.post(t(
                "msg.bot_request_audit", id=request.id, owner=owner, name=name, action=action,
                plugin_id=info.plugin_id, version=info.version, sha256=info.sha256,
                deps=", ".join(info.dependencies) or "-",
            ))
        return request

    async def approve(self, request_id: int, admin: UserID) -> int:
        """Mark approved, submit a BOT_PROVISION job reporting to the owner's DM. Returns the job ID."""
        from .jobs import JobKind

        request = await self.bot.store.get_bot_request(request_id)
        if request is None or request.state != "pending":
            raise NotFound("err.bot_request_unknown", id=request_id)
        await self.bot.store.set_bot_request_state(request_id, "approved", decided_by=admin)
        room_id = await self.bot.ops.open_dm(request.owner)
        plan = Plan(
            code="", kind=JobKind.BOT_PROVISION.value, requester=admin, room_id=room_id,
            summary="", items=[{"request_id": request_id, "label": request.name}],
            context={"bot": request.name}, expires_at=datetime.now(timezone.utc),
        )
        return await self.bot.jobs.submit(plan)

    async def reject(self, request_id: int, admin: UserID, reason: str | None) -> None:
        request = await self.bot.store.get_bot_request(request_id)
        if request is None or request.state != "pending":
            raise NotFound("err.bot_request_unknown", id=request_id)
        await self.bot.store.set_bot_request_state(request_id, "rejected", decided_by=admin, reason=reason,
                                                   drop_package=True)
        await self.bot.audit.log(admin, "bot.reject", bot=request.name, target_count=1)
        room_id = await self.bot.ops.open_dm(request.owner)
        await self.bot.ops.send(room_id, t("msg.bot_rejected", name=request.name, reason=reason or "-"))

    async def provision_item(self, job: Job, item: JobItem) -> None:
        """Plan §10 step 5. Safe to re-run after an interruption."""
        request = await self.bot.store.get_bot_request(item.payload["request_id"])
        if request is None:
            raise NotFound("err.bot_request_unknown", id=item.payload["request_id"])
        try:
            if request.action == "create":
                await self._provision_create(request)
            else:
                await self._provision_update(request)
        except Exception:
            await self.bot.store.set_bot_request_state(request.id, "failed")
            raise
        await self.bot.store.set_bot_request_state(request.id, "done", drop_package=True)

    async def _provision_create(self, request: BotRequest) -> None:
        synapse = self.bot.synapse
        user_id = self.bot_user_id(request.owner, request.name)
        password = secrets.token_urlsafe(32)
        displayname = f"{request.name} ({request.owner[1:].split(':', 1)[0]})"
        existing = await synapse.get_user(user_id)
        if existing and existing.get("deactivated"):
            await synapse.reactivate(user_id, password)
        elif existing:
            await synapse.reset_password(user_id, password, logout_devices=True)
        else:
            await synapse.create_user(user_id, password, displayname, user_type="bot")
        if self.cfg["user_bots.ratelimit_exempt"]:
            await synapse.override_ratelimit(user_id)
        access_token, device_id = await synapse.login(user_id, password, "maubot")
        del password
        await self.client.upload_plugin(request.mbp)
        await self.bot.store.set_plugin_owner(request.plugin_id, request.owner)
        await self.client.save_client(user_id, self.cfg["user_bots.homeserver_url"], access_token, device_id,
                                      displayname)
        instance_id = self.instance_id(request.owner, request.name)
        await self.client.put_instance(instance_id, type=request.plugin_id, primary_user=user_id,
                                       enabled=True, started=True)
        await self.bot.store.save_user_bot(UserBot(
            owner=request.owner, name=request.name, plugin_id=request.plugin_id, instance_id=instance_id,
            user_id=user_id, sha256=request.sha256, state="running",
        ))
        room_id = await self.bot.ops.open_dm(request.owner)
        await self.bot.ops.send(room_id, t("msg.bot_ready", name=request.name, user_id=user_id))

    async def _provision_update(self, request: BotRequest) -> None:
        bot = await self.bot.store.get_user_bot(request.owner, request.name)
        if bot is None:
            raise NotFound("err.bot_unknown", name=request.name)
        await self.client.upload_plugin(request.mbp)
        await self.bot.store.set_plugin_owner(request.plugin_id, request.owner)
        if request.plugin_id != bot.plugin_id:
            await self.client.put_instance(bot.instance_id, type=request.plugin_id)
        old_plugin = bot.plugin_id
        bot.plugin_id, bot.sha256 = request.plugin_id, request.sha256
        await self.bot.store.save_user_bot(bot)
        if old_plugin != request.plugin_id and not await self.bot.store.instances_using_plugin(old_plugin):
            await self.client.delete_plugin(old_plugin)
            await self.bot.store.delete_plugin_owner(old_plugin)
        room_id = await self.bot.ops.open_dm(request.owner)
        await self.bot.ops.send(room_id, t("msg.bot_updated", name=request.name, version=request.version))

    async def delete_item(self, job: Job, item: JobItem) -> None:
        """Delete instance and client, deactivate the account, delete the plugin if unused."""
        bot = await self.bot.store.get_user_bot(item.payload["owner"], item.payload["name"])
        if bot is None:
            raise NotFound("err.bot_unknown", name=item.payload["name"])
        await self.client.delete_instance(bot.instance_id)
        await self.client.delete_client(bot.user_id)
        if await self.bot.synapse.user_exists(bot.user_id):
            await self.bot.synapse.deactivate(bot.user_id)
        await self.bot.store.set_user_bot_state(bot.owner, bot.name, "deleted")
        if not await self.bot.store.instances_using_plugin(bot.plugin_id):
            await self.client.delete_plugin(bot.plugin_id)
            await self.bot.store.delete_plugin_owner(bot.plugin_id)

    async def get(self, owner: UserID, name: str) -> UserBot:
        bot = await self.bot.store.get_user_bot(owner, name)
        if bot is None:
            raise NotFound("err.bot_unknown", name=name)
        return bot

    async def info(self, owner: UserID, name: str) -> dict[str, Any]:
        bot = await self.get(owner, name)
        instance = await self.client.get_instance(bot.instance_id) or {}
        plugin = await self.client.get_plugin(bot.plugin_id) or {}
        rooms = await self.bot.synapse.joined_room_count(bot.user_id)
        return {
            "name": bot.name, "user_id": bot.user_id, "plugin_id": bot.plugin_id,
            "version": plugin.get("version", "?"), "started": bool(instance.get("started")),
            "enabled": bool(instance.get("enabled")), "rooms": rooms,
        }

    async def set_started(self, owner: UserID, name: str, started: bool) -> None:
        bot = await self.get(owner, name)
        await self.client.put_instance(bot.instance_id, started=started)
        await self.bot.store.set_user_bot_state(owner, name, "running" if started else "stopped")

    async def get_config(self, owner: UserID, name: str) -> str:
        bot = await self.get(owner, name)
        instance = await self.client.get_instance(bot.instance_id) or {}
        return instance.get("config") or ""

    async def set_config(self, owner: UserID, name: str, yaml: str) -> None:
        bot = await self.get(owner, name)
        try:
            YAML(typ="safe").load(yaml)
        except YAMLError as e:
            raise ValidationError("err.yaml_invalid", detail=str(e).splitlines()[0]) from e
        await self.client.put_instance(bot.instance_id, config=yaml)

    async def list_for(self, owner: UserID | None) -> list[UserBot]:
        return await self.bot.store.list_user_bots(owner)

    async def disable_all(self) -> int:
        count = await self.client.stop_all()
        for bot in await self.bot.store.list_user_bots():
            await self.bot.store.set_user_bot_state(bot.owner, bot.name, "stopped")
        return count
