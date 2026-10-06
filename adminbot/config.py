"""Instance config (defaults in base-config.yaml, edited per environment in the maubot UI)."""
from __future__ import annotations

from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

# Every leaf (or list) in base-config.yaml. Values from the existing config win.
KEYS = [
    "admins",
    "professors",
    "server_name",
    "audit_room",
    "archive_space",
    "language",
    "startup.self_check",
    "identity.allowed_domains",
    "identity.bind_threepids",
    "signup.public_base_url",
    "signup.login_url",
    "signup.token_ttl_days",
    "signup.reset_ttl_hours",
    "signup.min_password_length",
    "signup.trusted_proxies",
    "signup.rate_limit.per_ip_per_hour",
    "signup.rate_limit.global_per_hour",
    "invites.mode_existing",
    "invites.mode_new",
    "power_levels.owner",
    "power_levels.ta",
    "confirm.ttl_minutes",
    "jobs.delay_seconds",
    "uploads.wait_minutes",
    "uploads.max_size_mb",
    "user_bots.enabled",
    "user_bots.maubot_url",
    "user_bots.username",
    "user_bots.password",
    "user_bots.homeserver_url",
    "user_bots.require_approval",
    "user_bots.max_per_professor",
    "user_bots.max_mbp_size_mb",
    "user_bots.ratelimit_exempt",
]


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        for key in KEYS:
            helper.copy(key)
