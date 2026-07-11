"""Tests for PCR sentiment color gradient."""

from __future__ import annotations

from optionchain.display import format_pcr, pcr_sentiment_style


def test_pcr_none_is_dim():
    assert pcr_sentiment_style(None) == "dim"
    assert format_pcr(None).plain == "—"


def test_pcr_optimistic_is_greenish():
    style = pcr_sentiment_style(0.45)
    assert style.startswith("bold rgb(")
    r, g, b = _parse_rgb(style)
    # Optimistic: green channel should dominate red
    assert g > r
    assert g > b


def test_pcr_balanced_is_amberish():
    style = pcr_sentiment_style(1.0)
    r, g, b = _parse_rgb(style)
    # Amber/yellow: high red+green, low blue
    assert r > 150 and g > 120
    assert b < 80


def test_pcr_cautious_is_reddish():
    style = pcr_sentiment_style(1.8)
    r, g, b = _parse_rgb(style)
    assert r > g
    assert r > b


def test_pcr_gradient_monotone_red_channel():
    """As PCR rises (more puts), red should generally increase."""
    samples = [0.5, 0.8, 1.0, 1.3, 2.0]
    reds = [_parse_rgb(pcr_sentiment_style(x))[0] for x in samples]
    # Not strictly required to be strictly increasing every step, but endpoints clear
    assert reds[-1] > reds[0]


def test_format_pcr_digits():
    text = format_pcr(0.798, digits=3)
    assert text.plain == "0.798"


def _parse_rgb(style: str) -> tuple[int, int, int]:
    # "bold rgb(R,G,B)"
    body = style.split("rgb(")[1].rstrip(")")
    r, g, b = body.split(",")
    return int(r), int(g), int(b)
