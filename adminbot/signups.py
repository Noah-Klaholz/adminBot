"""Signup and password-reset links (plan §4). Shared by the !signup/!invite commands and the webapp.

Tokens: 32 random bytes, URL-safe; only the SHA-256 hash is stored; single use.
Signup links expire after signup.token_ttl_days, reset links after signup.reset_ttl_hours.
A user may have several open signup links (e.g. from two professors); the first one used
creates the account, applies ALL pending invites and invalidates the others.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import logging
import secrets

from mautrix.types import UserID

from .audit import Audit
from .config import Config
from .db import PendingInvite, SignupToken, Store
from .delivery import SignupLink
from .errors import NotFound, ValidationError
from .matrix_ops import MatrixOps
from .strings import t
from .synapse_admin import SynapseAdmin

log = logging.getLogger("maubot.adminbot.signups")


class TokenKind(str, Enum):
    SIGNUP = "signup"
    RESET = "reset"


def new_token() -> tuple[str, str]:
    """(raw token for the link, sha256 hex for the DB)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_password(password: str, confirm: str, min_length: int) -> str | None:
    """None if fine, otherwise a strings key. The admin API skips Synapse's password policy,
    so this is the only check."""
    if password != confirm:
        return "web.error.password_mismatch"
    if len(password) < min_length:
        return "web.error.password_short"
    return None


class SignupService:
    def __init__(self, store: Store, synapse: SynapseAdmin, ops: MatrixOps, audit: Audit, config: Config) -> None:
        self.store = store
        self.synapse = synapse
        self.ops = ops
        self.audit = audit
        self.config = config

    def link_for(self, raw_token: str, kind: TokenKind) -> str:
        base = (self.config["signup.public_base_url"] or "").rstrip("/")
        if not base:
            raise ValidationError("err.no_public_url")
        return f"{base}/{kind.value}?t={raw_token}"

    async def _issue(self, kind: TokenKind, user_id: UserID, emails: list[str], created_by: UserID,
                     course: str | None, ttl: timedelta) -> SignupLink:
        raw, token_hash = new_token()
        now = datetime.now(timezone.utc)
        link = self.link_for(raw, kind)
        await self.store.add_token(SignupToken(
            token_hash=token_hash, kind=kind.value, user_id=user_id, emails=emails, course=course,
            created_by=created_by, created_at=now, expires_at=now + ttl, used_at=None,
        ))
        return SignupLink(email=", ".join(emails), user_id=user_id, link=link, course=course, kind=kind.value)

    @property
    def signup_ttl(self) -> timedelta:
        return timedelta(days=self.config["signup.token_ttl_days"])

    async def create(self, user_id: UserID, emails: list[str], created_by: UserID,
                     course: str | None = None) -> SignupLink:
        """New signup link plus a pending invite for `course`. Fails if the account exists."""
        if await self.synapse.user_exists(user_id):
            raise ValidationError("err.account_exists", user_id=user_id)
        if course:
            await self.store.add_pending_invite(PendingInvite(
                user_id=user_id, course=course, mode=self.config["invites.mode_new"], created_by=created_by,
            ))
        return await self._issue(TokenKind.SIGNUP, user_id, emails, created_by, course, self.signup_ttl)

    async def renew(self, user_id: UserID, by: UserID) -> SignupLink:
        """New link, old ones invalid; keeps emails (merged) and course of the newest."""
        tokens = await self.store.tokens_for(user_id, TokenKind.SIGNUP.value)
        if not tokens:
            raise NotFound("err.no_open_link", user_id=user_id)
        emails: list[str] = []
        for token in tokens:
            emails.extend(e for e in token.emails if e not in emails)
        await self.store.delete_tokens_for(user_id, TokenKind.SIGNUP.value)
        link = await self._issue(TokenKind.SIGNUP, user_id, emails, by, tokens[-1].course, self.signup_ttl)
        await self.audit.log(by, "signup.renew", course=tokens[-1].course, target_count=1)
        return link

    async def revoke(self, user_id: UserID, by: UserID) -> int:
        """Delete all open signup links and pending invites of the user."""
        count = await self.store.delete_tokens_for(user_id, TokenKind.SIGNUP.value)
        await self.store.delete_pending_invites(user_id)
        if count:
            await self.audit.log(by, "signup.revoke", target_count=1)
        return count

    async def open_tokens(self, user_id: UserID) -> list[SignupToken]:
        return await self.store.tokens_for(user_id, TokenKind.SIGNUP.value)

    async def list_open(self, course: str | None = None) -> list[SignupToken]:
        return await self.store.list_open_tokens(TokenKind.SIGNUP.value, course)

    async def create_reset(self, user_id: UserID, by: UserID) -> SignupLink:
        """Admins only (checked by the command). The account must exist. Audited."""
        if not await self.synapse.user_exists(user_id):
            raise NotFound("err.user_not_found", user_id=user_id)
        await self.store.delete_tokens_for(user_id, TokenKind.RESET.value)
        ttl = timedelta(hours=self.config["signup.reset_ttl_hours"])
        link = await self._issue(TokenKind.RESET, user_id, [], by, None, ttl)
        await self.audit.log(by, "reset.create", target_count=1)
        return link

    async def lookup(self, raw_token: str, kind: TokenKind) -> SignupToken | None:
        """Valid = right kind, unused, not expired."""
        if not raw_token or len(raw_token) > 100:
            return None
        token = await self.store.get_token(hash_token(raw_token))
        if (
            token is None
            or token.kind != kind.value
            or token.used_at is not None
            or token.expires_at <= datetime.now(timezone.utc)
        ):
            return None
        return token

    async def redeem_signup(self, raw_token: str, displayname: str, password: str) -> UserID:
        """1. burn token (atomic) 2. create user (+threepids) 3. apply pending invites 4. audit.
        If creating the user fails, the token is un-burned so the link keeps working."""
        token = await self.lookup(raw_token, TokenKind.SIGNUP)
        if token is None or not await self.store.burn_token(token.token_hash):
            raise ValidationError("web.error.invalid")
        try:
            emails = token.emails if self.config["identity.bind_threepids"] else None
            await self.synapse.create_user(token.user_id, password, displayname.strip() or None, emails)
        except Exception:
            await self.store.unburn_token(token.token_hash)
            raise
        await self.store.delete_tokens_for(token.user_id, TokenKind.SIGNUP.value)
        await self.audit.log(token.user_id, "signup.redeem", course=token.course, target_count=1)
        try:
            await self.apply_pending_invites(token.user_id)
        except Exception:
            log.exception(f"Applying pending invites for {token.user_id} failed")
        return token.user_id

    async def redeem_reset(self, raw_token: str, password: str) -> UserID:
        token = await self.lookup(raw_token, TokenKind.RESET)
        if token is None or not await self.store.burn_token(token.token_hash):
            raise ValidationError("web.error.invalid")
        try:
            await self.synapse.reset_password(token.user_id, password, logout_devices=True)
        except Exception:
            await self.store.unburn_token(token.token_hash)
            raise
        await self.store.delete_tokens_for(token.user_id, TokenKind.RESET.value)
        await self.audit.log(token.created_by, "reset.redeem", target_count=1)
        try:
            room_id = await self.ops.open_dm(token.user_id)
            await self.ops.send(room_id, t("msg.password_reset_notice", admin=token.created_by))
        except Exception:
            log.exception(f"Couldn't tell {token.user_id} about the password reset")
        return token.user_id

    async def apply_pending_invites(self, user_id: UserID) -> None:
        """force_join: into the space and every non-staff, non-group room.
        invite: an invitation to the space (restricted rooms open after accepting)."""
        for invite in await self.store.pending_invites(user_id):
            course = await self.store.get_course(invite.course)
            if course is None or course.archived:
                continue
            if invite.mode == "force_join":
                await self.synapse.force_join(user_id, course.space_id)
                for room_id in await self.ops.children(course.space_id):
                    tag = await self.ops.tag(room_id) or {}
                    if tag.get("kind") == "room" and tag.get("members", "all") == "all":
                        try:
                            await self.synapse.force_join(user_id, room_id)
                        except Exception as e:
                            log.warning(f"Force-join of {user_id} into {room_id} failed: {e}")
            else:
                await self.ops.invite(course.space_id, user_id)
        await self.store.delete_pending_invites(user_id)

    async def expire(self) -> int:
        return await self.store.expire_tokens()
