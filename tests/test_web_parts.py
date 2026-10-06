"""Pure parts of the webapp and link delivery."""
from aiohttp.test_utils import make_mocked_request

from adminbot.delivery import CsvDelivery, InlineDelivery, SignupLink
from adminbot.signups import check_password, hash_token, new_token
from adminbot.web.pages import signup_form
from adminbot.web.ratelimit import RateLimiter, client_ip, parse_networks


def test_token_is_random_and_only_hash_is_stored():
    raw, token_hash = new_token()
    assert len(raw) >= 43 and raw != token_hash
    assert hash_token(raw) == token_hash
    assert new_token()[0] != raw


def test_check_password():
    assert check_password("a" * 12, "b" * 12, 12) == "web.error.password_mismatch"
    assert check_password("short", "short", 12) == "web.error.password_short"
    assert check_password("a" * 12, "a" * 12, 12) is None


def test_rate_limiter():
    now = [0.0]
    limiter = RateLimiter(per_ip_per_hour=2, global_per_hour=3, clock=lambda: now[0])
    assert limiter.allow("1.1.1.1") and limiter.allow("1.1.1.1")
    assert not limiter.allow("1.1.1.1")
    assert limiter.allow("2.2.2.2")
    assert not limiter.allow("3.3.3.3")  # global limit
    now[0] = 3601
    assert limiter.allow("1.1.1.1")


def test_client_ip_trusts_only_proxies():
    trusted = parse_networks(["172.16.0.0/12"])
    via_proxy = make_mocked_request("GET", "/", headers={"X-Forwarded-For": "9.9.9.9, 131.152.1.2"},
                                    transport=_transport("172.18.0.1"))
    assert client_ip(via_proxy, trusted) == "131.152.1.2"
    direct = make_mocked_request("GET", "/", headers={"X-Forwarded-For": "6.6.6.6"},
                                 transport=_transport("8.8.8.8"))
    assert client_ip(direct, trusted) == "8.8.8.8"


def _transport(ip):
    class Transport:
        def get_extra_info(self, name, default=None):
            return (ip, 12345) if name == "peername" else default

        def is_closing(self):
            return False

    return Transport()


def test_signup_form_escapes():
    html = signup_form('@x:y"><script>', "tok", "nonce", 12, error="<b>")
    assert "<script>" not in html and "&lt;b&gt;" in html


def test_csv_delivery_has_bom_and_rows():
    links = [SignupLink("a@unibas.ch", "@a:x", "https://l/1", "CS101")]
    result = CsvDelivery("https://x").deliver(links)
    name, data, mime = result.files[0]
    assert name == "signup-links.csv" and mime == "text/csv"
    assert data.decode("utf-8").startswith("﻿email,user_id,link,course")
    assert "{link}" in result.text


def test_inline_delivery():
    text = InlineDelivery("https://x").deliver([SignupLink("a@unibas.ch", "@a:x", "https://l/1", None)]).text
    assert "https://l/1" in text and "@a:x" in text
