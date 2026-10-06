"""Webapp handler classes, served under /_matrix/maubot/plugin/<instance>/. Registered like
the command classes. Host nginx exposes only this prefix (server/docs/admin-bot-setup.md)."""
from .signup import SignupWeb

WEB_CLASSES = [SignupWeb]
