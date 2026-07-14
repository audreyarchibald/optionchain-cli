"""Tests for big-cap universe helpers."""

from optionchain.universe import (
    BIG_CAP_SYMBOLS,
    BIG_CAPS,
    big_cap_choices,
    parse_big_cap_choice,
)


def test_big_caps_nonempty_unique():
    assert len(BIG_CAPS) >= 30
    assert len(BIG_CAP_SYMBOLS) == len(set(BIG_CAP_SYMBOLS))
    assert "AAPL" in BIG_CAP_SYMBOLS
    assert "SPY" in BIG_CAP_SYMBOLS
    assert "NVDA" in BIG_CAP_SYMBOLS


def test_choices_and_parse():
    choices = big_cap_choices()
    assert choices[0].startswith("SPY")
    assert parse_big_cap_choice("AAPL — Apple") == "AAPL"
    assert parse_big_cap_choice("tsla") == "TSLA"
    assert parse_big_cap_choice("BRK-B — Berkshire B") == "BRK-B"
