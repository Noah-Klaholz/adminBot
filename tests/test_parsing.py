import pytest

from adminbot.parsing import (
    ArgError,
    decode_text,
    extract_emails,
    parse_group_assignments,
    parse_group_posts,
    read_csv,
    split_args,
    split_first_line,
)


def test_split_args_quotes_and_flags():
    a = split_args('CS101 "Intro to CS" --semester HS26 --dry-run', {"semester": True, "dry-run": False})
    assert a.positionals == ["CS101", "Intro to CS"]
    assert a.value("semester") == "HS26"
    assert a.flag("dry-run")


def test_split_args_equals_and_smart_quotes():
    a = split_args("CS101 “Intro to CS” --semester=FS27", {"semester": True})
    assert a.positionals == ["CS101", "Intro to CS"]
    assert a.value("semester") == "FS27"


def test_split_args_unknown_flag_and_bad_quotes():
    with pytest.raises(ArgError):
        split_args("x --nope", {})
    with pytest.raises(ArgError):
        split_args('x "unterminated', {})
    with pytest.raises(ArgError):
        split_args("x --semester", {"semester": True})


def test_require():
    with pytest.raises(ArgError):
        split_args("one").require(2, "usage")


def test_split_first_line():
    assert split_first_line("CS101 --dry-run\na@unibas.ch\nb@unibas.ch") == (
        "CS101 --dry-run", "a@unibas.ch\nb@unibas.ch")


def test_extract_emails():
    text = "Max <max.muster@unibas.ch>; anna@stud.unibas.ch, garbage, x@y"
    assert extract_emails(text) == ["max.muster@unibas.ch", "anna@stud.unibas.ch"]


def test_decode_text():
    assert decode_text("﻿a;b".encode("utf-8")) == "a;b"
    assert decode_text("Müller".encode("cp1252")) == "Müller"


def test_read_csv_semicolon_bom_and_case():
    rows = read_csv("﻿Email;Group\r\na@unibas.ch;1\n\n b@unibas.ch ; 2 \n", ["email", "group"])
    assert rows == [{"email": "a@unibas.ch", "group": "1"}, {"email": "b@unibas.ch", "group": "2"}]


def test_read_csv_missing_column():
    with pytest.raises(ArgError) as e:
        read_csv("email\na@unibas.ch", ["email", "group"])
    assert e.value.params == {"column": "group"}


def test_parse_group_assignments_with_tas():
    text = "email,group,role\na@unibas.ch,1,\n@ta:example.com,2,ta\n"
    assert parse_group_assignments(text) == [("a@unibas.ch", 1, "student"), ("@ta:example.com", 2, "ta")]
    with pytest.raises(ArgError):
        parse_group_assignments("email,group\na@unibas.ch,zero\n")


def test_parse_group_posts():
    assert parse_group_posts("group,message\n1,Hello\n2,\n") == [("group", "1", "Hello")]
    assert parse_group_posts("email;message\na@unibas.ch;12 points") == [("email", "a@unibas.ch", "12 points")]
