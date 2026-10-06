"""Exceptions shared by services and command handlers. Each carries a strings key for the reply."""
from __future__ import annotations


class AdminBotError(Exception):
    """Base class. `key` is a strings key, `params` its placeholders."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params

    def __str__(self) -> str:
        from .strings import t

        return t(self.key, **self.params)


class Forbidden(AdminBotError):
    """The sender lacks the required role."""


class NotFound(AdminBotError):
    """Course, room, template, token, bot ... doesn't exist."""


class ValidationError(AdminBotError):
    """Bad input: arguments, CSV, template YAML, .mbp package, password."""


class ConfirmError(AdminBotError):
    """Unknown, expired or foreign confirm code."""


class CheckFailed(AdminBotError):
    """A command depends on a startup check that failed."""
