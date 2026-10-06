import pytest

from adminbot.identity import (
    IdentityError,
    group_by_user,
    guest_user_id,
    is_local_user,
    normalize_email,
    user_id_for_email,
)

DOMAINS = ["unibas.ch", "stud.unibas.ch"]
SERVER = "matrix.dmi.unibas.ch"


def test_localpart_is_lowercased():
    assert user_id_for_email("Max.Muster@unibas.ch", SERVER, DOMAINS) == f"@max.muster:{SERVER}"


def test_normalize_strips_wrapping():
    assert normalize_email("  <mailto:Max.Muster@Unibas.ch>, ") == "max.muster@unibas.ch"


def test_both_domains_map_to_one_user():
    users, invalid = group_by_user(
        ["max.muster@unibas.ch", "Max.Muster@stud.unibas.ch", "max.muster@unibas.ch"], SERVER, DOMAINS)
    assert users == {f"@max.muster:{SERVER}": ["max.muster@unibas.ch", "max.muster@stud.unibas.ch"]}
    assert invalid == []


@pytest.mark.parametrize("email,key", [
    ("x@gmail.com", "err.email_domain"),
    ("max+1@unibas.ch", "err.email_chars"),
    ("müller@unibas.ch", "err.email_chars"),
    ("adminbot@unibas.ch", "err.email_reserved"),
    ("bot.x@unibas.ch", "err.email_reserved"),
    ("guest.x@stud.unibas.ch", "err.email_reserved"),
    ("not-an-email", "err.email_invalid"),
])
def test_rejected(email, key):
    with pytest.raises(IdentityError) as e:
        user_id_for_email(email, SERVER, DOMAINS)
    assert e.value.key == key


def test_subdomain_is_not_allowed():
    with pytest.raises(IdentityError):
        user_id_for_email("x@evil.unibas.ch", SERVER, DOMAINS)


def test_guest_user_id():
    assert guest_user_id("Jane Doe", SERVER) == f"@guest.jane.doe:{SERVER}"
    with pytest.raises(IdentityError):
        guest_user_id("Jane/Doe", SERVER)


def test_group_by_user_reports_invalid_entries():
    users, invalid = group_by_user(["a@unibas.ch", "b@gmail.com", "b@gmail.com"], SERVER, DOMAINS)
    assert list(users) == [f"@a:{SERVER}"]
    assert invalid == ["b@gmail.com"]


def test_is_local_user():
    assert is_local_user(f"@a:{SERVER}", SERVER)
    assert not is_local_user("@a:evil.org", SERVER)
    assert not is_local_user("a", SERVER)
    assert not is_local_user(f"@:{SERVER}", SERVER)
