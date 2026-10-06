"""User-facing texts (plan §8). Handlers never contain literal text; they call t(key, **params)."""
from __future__ import annotations

from .en import STRINGS as EN

LANGUAGES: dict[str, dict[str, str]] = {"en": EN}
_current: dict[str, str] = EN


def set_language(language: str) -> None:
    """Switch to LANGUAGES[language]; unknown languages fall back to English."""
    global _current
    _current = LANGUAGES.get(language, EN)


def t(key: str, **params: object) -> str:
    """Look up `key` (falling back to English, then to the key itself) and format it."""
    text = _current.get(key) or EN.get(key) or key
    return text.format(**params) if params else text
