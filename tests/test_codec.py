"""Tests for ISO-8601 duration helpers."""

import pytest

from psnfamily.codec import format_pt, parse_pt, quantize_seconds


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("PT0M", 0),
        ("PT15M", 900),
        ("PT30M", 1800),
        ("PT1H", 3600),
        ("PT1H30M", 5400),
        ("PT24H", 86400),
        ("PT90M", 5400),
        ("-PT30M", -1800),
        ("PT-30M", -1800),
        ("", 0),
        (None, 0),
    ],
)
def test_parse_pt(text, seconds):
    assert parse_pt(text) == seconds


def test_parse_pt_invalid():
    with pytest.raises(ValueError):
        parse_pt("30m")


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "PT0M"),
        (900, "PT15M"),
        (5400, "PT1H30M"),
        (-1800, "-PT30M"),
        (3600, "PT1H"),
        (86400, "PT24H"),
    ],
)
def test_format_pt(seconds, text):
    assert format_pt(seconds) == text


def test_format_parse_roundtrip():
    for s in (-7200, -900, 0, 900, 3600, 9000):
        assert parse_pt(format_pt(s)) == s


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, 0), (449, 0), (451, 900), (900, 900), (1000, 900), (1400, 1800)],
)
def test_quantize(seconds, expected):
    assert quantize_seconds(seconds) == expected
