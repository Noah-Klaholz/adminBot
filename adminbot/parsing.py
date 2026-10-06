"""Parsing of command arguments and uploaded lists. Pure functions, no I/O.

All commands receive their arguments as one raw string and parse them here, so quoting and
--flags behave the same everywhere.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
import io
import re
import shlex

from .errors import ValidationError

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'=-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "‘": "'", "’": "'"})


class ArgError(ValidationError):
    """Bad arguments."""


@dataclass
class Args:
    positionals: list[str] = field(default_factory=list)
    flags: dict[str, str | bool] = field(default_factory=dict)

    def require(self, n: int, usage: str) -> list[str]:
        """First n positionals or ArgError."""
        if len(self.positionals) < n:
            raise ArgError("err.bad_args", usage=usage)
        return self.positionals[:n]

    def get(self, index: int, default: str | None = None) -> str | None:
        return self.positionals[index] if len(self.positionals) > index else default

    def flag(self, name: str) -> bool:
        return bool(self.flags.get(name, False))

    def value(self, name: str, default: str | None = None) -> str | None:
        value = self.flags.get(name)
        return value if isinstance(value, str) else default


def split_args(raw: str, known_flags: dict[str, bool] | None = None, usage: str = "") -> Args:
    """shlex-split `raw`. known_flags maps flag name -> takes a value.
    '--semester HS26' / '--semester=HS26' -> flags['semester'] = 'HS26'; '--dry-run' -> True."""
    known_flags = known_flags or {}
    try:
        tokens = shlex.split((raw or "").translate(SMART_QUOTES))
    except ValueError as e:
        raise ArgError("err.bad_args", usage=usage) from e
    args = Args()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("--") and len(token) > 2:
            name, _, inline = token[2:].partition("=")
            if name not in known_flags:
                raise ArgError("err.unknown_flag", flag=token, usage=usage)
            if known_flags[name]:
                if inline:
                    args.flags[name] = inline
                elif i + 1 < len(tokens):
                    i += 1
                    args.flags[name] = tokens[i]
                else:
                    raise ArgError("err.bad_args", usage=usage)
            else:
                args.flags[name] = True
        else:
            args.positionals.append(token)
        i += 1
    return args


def split_first_line(raw: str) -> tuple[str, str]:
    """('first line', 'rest') - commands like !invite take arguments on the first line and a
    pasted list below it."""
    first, _, rest = (raw or "").partition("\n")
    return first, rest


def extract_emails(text: str) -> list[str]:
    """All email-like tokens from free text or a .txt/.csv upload, in order."""
    return EMAIL_RE.findall(text or "")


def decode_text(data: bytes) -> str:
    """Uploaded text files: UTF-8 (with or without BOM), falling back to Windows-1252 (old Excel)."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_csv(text: str, columns: list[str], optional: list[str] | None = None) -> list[dict[str, str]]:
    """CSV with a header row; ',' or ';' (Excel in de-CH) as separator, BOM stripped, header
    case-insensitive. Raises ArgError if a required column is missing. Empty rows are skipped."""
    text = text.lstrip("﻿")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ArgError("err.csv_empty")
    delimiter = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    header = [h.strip().lower() for h in next(reader)]
    for column in columns:
        if column not in header:
            raise ArgError("err.csv_missing_column", column=column)
    wanted = columns + [c for c in (optional or []) if c in header]
    rows = []
    for values in reader:
        row = {name: (values[header.index(name)].strip() if header.index(name) < len(values) else "")
               for name in wanted}
        if any(row.values()):
            rows.append(row)
    return rows


def _header(text: str) -> list[str]:
    first = next((line for line in text.lstrip("﻿").splitlines() if line.strip()), "")
    delimiter = ";" if first.count(";") > first.count(",") else ","
    return [h.strip().lower() for h in first.split(delimiter)]


def parse_group_number(value: str) -> int:
    try:
        number = int(value.strip())
    except ValueError as e:
        raise ArgError("err.group_number", value=value) from e
    if number < 1:
        raise ArgError("err.group_number", value=value)
    return number


def parse_group_assignments(text: str) -> list[tuple[str, int, str]]:
    """CSV with columns 'email' (or 'user'), 'group' and optional 'role' (student|ta).
    The identifier may be an email or a Matrix ID. -> [(identifier, group, role)]."""
    header = _header(text)
    id_column = "email" if "email" in header else "user"
    rows = read_csv(text, [id_column, "group"], optional=["role"])
    result = []
    for row in rows:
        role = (row.get("role") or "student").lower()
        if role not in ("student", "ta"):
            raise ArgError("err.csv_role", value=role)
        result.append((row[id_column], parse_group_number(row["group"]), role))
    return result


def parse_group_posts(text: str) -> list[tuple[str, str, str]]:
    """CSV 'group,message' or 'email,message' -> [(kind 'group'|'email', target, message)]."""
    header = _header(text)
    if "group" in header:
        rows = read_csv(text, ["group", "message"])
        return [("group", str(parse_group_number(r["group"])), r["message"]) for r in rows if r["message"]]
    rows = read_csv(text, ["email", "message"])
    return [("email", r["email"], r["message"]) for r in rows if r["message"]]
