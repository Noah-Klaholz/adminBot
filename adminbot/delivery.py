"""How links reach people without SMTP (plan §4). The bot only produces links; people deliver them."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
import io
from typing import Protocol

from mautrix.types import UserID

from .strings import t


@dataclass
class SignupLink:
    email: str
    user_id: UserID
    link: str
    course: str | None
    kind: str = "signup"  # "signup" | "reset"


@dataclass
class DeliveryResult:
    text: str  # reply text (instructions / message template)
    files: list[tuple[str, bytes, str]] = field(default_factory=list)  # (filename, data, mimetype)


class Delivery(Protocol):
    def deliver(self, links: list[SignupLink]) -> DeliveryResult: ...


class CsvDelivery:
    """Default for bulk: signup-links.csv (email, user_id, link, course) + a mail-merge text."""

    def __init__(self, login_url: str) -> None:
        self.login_url = login_url

    def deliver(self, links: list[SignupLink]) -> DeliveryResult:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["email", "user_id", "link", "course"])
        for link in links:
            writer.writerow([link.email, link.user_id, link.link, link.course or ""])
        # UTF-8 with BOM so Excel opens it correctly.
        data = ("﻿" + buf.getvalue()).encode("utf-8")
        text = t("msg.csv_delivery", count=len(links), login_url=self.login_url)
        return DeliveryResult(text=text, files=[("signup-links.csv", data, "text/csv")])


class InlineDelivery:
    """Single links inline with a short text to forward (latecomers, guests, resets)."""

    def __init__(self, login_url: str) -> None:
        self.login_url = login_url

    def deliver(self, links: list[SignupLink]) -> DeliveryResult:
        parts = []
        for link in links:
            key = "msg.inline_reset" if link.kind == "reset" else "msg.inline_signup"
            parts.append(t(key, user_id=link.user_id, email=link.email or "-", link=link.link,
                           login_url=self.login_url))
        return DeliveryResult(text="\n\n".join(parts))
