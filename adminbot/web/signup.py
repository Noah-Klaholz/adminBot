"""Signup and password-reset pages (plan §4)."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from typing import TYPE_CHECKING

from aiohttp.web import Request, Response
from maubot.handlers import web

from ..errors import ValidationError
from ..signups import TokenKind, check_password, hash_token
from ..strings import t
from . import pages
from .ratelimit import RateLimiter, client_ip, parse_networks

if TYPE_CHECKING:
    from ..bot import AdminBot

log = logging.getLogger("maubot.adminbot.web")

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class SignupWeb:
    def __init__(self, bot: AdminBot) -> None:
        self.bot = bot
        self.limiter = RateLimiter(
            bot.config["signup.rate_limit.per_ip_per_hour"], bot.config["signup.rate_limit.global_per_hour"]
        )
        self.trusted = parse_networks(bot.config["signup.trusted_proxies"])
        self.nonce_key = secrets.token_bytes(32)

    @property
    def min_length(self) -> int:
        return self.bot.config["signup.min_password_length"]

    def nonce_for(self, token_hash: str) -> str:
        """HMAC of the token hash with a per-start random key (CSRF protection)."""
        return hmac.new(self.nonce_key, token_hash.encode(), hashlib.sha256).hexdigest()[:32]

    @staticmethod
    def html(body: str, status: int = 200) -> Response:
        return Response(text=body, status=status, content_type="text/html", charset="utf-8",
                         headers=SECURITY_HEADERS)

    def _limited(self, request: Request) -> Response | None:
        if not self.limiter.allow(client_ip(request, self.trusted)):
            return self.html(pages.error_page("web.error.rate_limited"), 429)
        return None

    async def _form(self, request: Request, kind: TokenKind) -> Response:
        limited = self._limited(request)
        if limited:
            return limited
        raw = request.query.get("t", "")
        token = await self.bot.signups.lookup(raw, kind)
        if token is None:
            return self.html(pages.error_page("web.error.invalid"), 400)
        nonce = self.nonce_for(token.token_hash)
        if kind == TokenKind.SIGNUP:
            return self.html(pages.signup_form(token.user_id, raw, nonce, self.min_length))
        return self.html(pages.reset_form(token.user_id, raw, nonce, self.min_length))

    async def _submit(self, request: Request, kind: TokenKind) -> Response:
        limited = self._limited(request)
        if limited:
            return limited
        form = await request.post()
        raw = str(form.get("t", ""))
        token = await self.bot.signups.lookup(raw, kind)
        if token is None or not hmac.compare_digest(str(form.get("nonce", "")), self.nonce_for(hash_token(raw))):
            return self.html(pages.error_page("web.error.invalid"), 400)
        password, confirm = str(form.get("password", "")), str(form.get("confirm", ""))
        displayname = str(form.get("displayname", ""))[:100]
        error_key = check_password(password, confirm, self.min_length)
        nonce = self.nonce_for(token.token_hash)
        if error_key:
            error = t(error_key, n=self.min_length)
            if kind == TokenKind.SIGNUP:
                body = pages.signup_form(token.user_id, raw, nonce, self.min_length, error, displayname)
            else:
                body = pages.reset_form(token.user_id, raw, nonce, self.min_length, error)
            return self.html(body, 400)
        login_url = self.bot.config["signup.login_url"]
        try:
            if kind == TokenKind.SIGNUP:
                user_id = await self.bot.signups.redeem_signup(raw, displayname, password)
                return self.html(pages.done(user_id, login_url))
            user_id = await self.bot.signups.redeem_reset(raw, password)
            return self.html(pages.done(user_id, login_url, "web.done_reset"))
        except ValidationError as e:
            log.info(f"{kind.value} for {token.user_id} refused: {e.key}")
            return self.html(pages.error_page("web.error.invalid"), 400)
        except Exception:
            log.exception(f"{kind.value} for {token.user_id} failed")
            return self.html(pages.error_page("web.error.generic"), 500)

    @web.get("/signup")
    async def signup_form(self, request: Request) -> Response:
        return await self._form(request, TokenKind.SIGNUP)

    @web.post("/signup")
    async def signup_submit(self, request: Request) -> Response:
        return await self._submit(request, TokenKind.SIGNUP)

    @web.get("/reset")
    async def reset_form(self, request: Request) -> Response:
        return await self._form(request, TokenKind.RESET)

    @web.post("/reset")
    async def reset_submit(self, request: Request) -> Response:
        return await self._submit(request, TokenKind.RESET)
