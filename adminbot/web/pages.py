"""Minimal HTML for the signup/reset pages. Inline CSS, no external assets, all values escaped."""
from __future__ import annotations

from html import escape

from mautrix.types import UserID

from ..strings import t

CSS = """
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--fg:#1d2329;--muted:#5c6670;--accent:#0b6bcb;--err:#b42318;--border:#d5dae0}
@media (prefers-color-scheme:dark){:root{--bg:#15191d;--card:#1e2328;--fg:#e8eaed;--muted:#9aa4ae;--accent:#5aa9f5;--err:#f97066;--border:#363d45}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:420px;margin:48px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px}
h1{font-size:1.3rem;margin:0 0 16px}p{margin:0 0 16px}code{font-size:.95em;word-break:break-all}
label{display:block;font-weight:600;margin:12px 0 4px}
input{width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--fg);font:inherit}
button{margin-top:20px;width:100%;padding:11px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}
.err{color:var(--err);font-weight:600}.muted{color:var(--muted);font-size:.9rem}
"""


def layout(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<meta name=\"robots\" content=\"noindex\"><title>{escape(title)}</title><style>{CSS}</style>"
        f"</head><body><main><div class=\"card\">{body}</div></main></body></html>"
    )


def _password_fields(min_length: int) -> str:
    return (
        f"<label for=\"pw\">{escape(t('web.password'))}</label>"
        f"<input id=\"pw\" name=\"password\" type=\"password\" autocomplete=\"new-password\" minlength=\"{min_length}\" required>"
        f"<label for=\"pw2\">{escape(t('web.password_confirm'))}</label>"
        f"<input id=\"pw2\" name=\"confirm\" type=\"password\" autocomplete=\"new-password\" minlength=\"{min_length}\" required>"
        f"<p class=\"muted\">{escape(t('web.password_hint', n=min_length))}</p>"
    )


def _error(error: str | None) -> str:
    return f"<p class=\"err\" role=\"alert\">{escape(error)}</p>" if error else ""


def signup_form(user_id: UserID, token: str, nonce: str, min_length: int, error: str | None = None,
                displayname: str = "") -> str:
    body = (
        f"<h1>{escape(t('web.title'))}</h1>"
        f"<p>{escape(t('web.signup.intro'))}<br><code>{escape(user_id)}</code></p>"
        f"{_error(error)}"
        "<form method=\"post\" action=\"\">"
        f"<input type=\"hidden\" name=\"t\" value=\"{escape(token)}\">"
        f"<input type=\"hidden\" name=\"nonce\" value=\"{escape(nonce)}\">"
        f"<label for=\"dn\">{escape(t('web.displayname'))}</label>"
        f"<input id=\"dn\" name=\"displayname\" maxlength=\"100\" autocomplete=\"name\" value=\"{escape(displayname)}\">"
        f"{_password_fields(min_length)}"
        f"<button type=\"submit\">{escape(t('web.submit.signup'))}</button></form>"
    )
    return layout(t("web.title"), body)


def reset_form(user_id: UserID, token: str, nonce: str, min_length: int, error: str | None = None) -> str:
    body = (
        f"<h1>{escape(t('web.title'))}</h1>"
        f"<p>{escape(t('web.reset.intro'))}<br><code>{escape(user_id)}</code></p>"
        f"{_error(error)}"
        "<form method=\"post\" action=\"\">"
        f"<input type=\"hidden\" name=\"t\" value=\"{escape(token)}\">"
        f"<input type=\"hidden\" name=\"nonce\" value=\"{escape(nonce)}\">"
        f"{_password_fields(min_length)}"
        f"<p class=\"muted\">{escape(t('web.reset.warning'))}</p>"
        f"<button type=\"submit\">{escape(t('web.submit.reset'))}</button></form>"
    )
    return layout(t("web.title"), body)


def done(user_id: UserID, login_url: str, key: str = "web.done") -> str:
    link = f"<a href=\"{escape(login_url)}\">{escape(login_url)}</a>" if login_url else ""
    body = (
        f"<h1>{escape(t('web.title'))}</h1>"
        f"<p>{escape(t(key))}</p>"
        f"<p>{escape(t('web.username'))}: <code>{escape(user_id)}</code></p>"
        f"<p>{escape(t('web.server'))}: {link}</p>"
        f"<p class=\"muted\">{escape(t('web.done_hint'))}</p>"
    )
    return layout(t("web.title"), body)


def error_page(key: str) -> str:
    """Generic texts only (web.error.*); never say whether a user or token exists."""
    return layout(t("web.title"), f"<h1>{escape(t('web.title'))}</h1><p class=\"err\">{escape(t(key))}</p>")
