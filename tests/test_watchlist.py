"""Tests for TradingView watchlist export."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from optionchain.fetcher import OptionChainError
from optionchain.leaders import LeadersResult, UnderlyingVolume
from optionchain.watchlist import (
    export_tradingview_watchlist,
    leaders_to_tradingview_lines,
    to_tradingview_symbol,
    yahoo_exchange_to_tv,
)


def test_yahoo_exchange_mapping():
    assert yahoo_exchange_to_tv("NMS") == "NASDAQ"
    assert yahoo_exchange_to_tv("NYQ") == "NYSE"
    assert yahoo_exchange_to_tv("NasdaqGS") == "NASDAQ"
    assert yahoo_exchange_to_tv("NYSEArca") == "AMEX"


def test_to_tradingview_symbol():
    assert to_tradingview_symbol("AAPL", exchange="NMS") == "NASDAQ:AAPL"
    assert to_tradingview_symbol("BRK-B", exchange="NYQ") == "NYSE:BRK.B"
    assert to_tradingview_symbol("SPY", with_exchange=False) == "SPY"
    assert to_tradingview_symbol("^VIX") == "TVC:VIX"
    assert to_tradingview_symbol("^UNKNOWN_INDEX") is None


def test_leaders_to_lines_unique_order():
    leaders = [
        UnderlyingVolume(1, "SPY", "SPY", 1, 0, 1, 0, 1, exchange="PCX"),
        UnderlyingVolume(2, "AAPL", "Apple", 1, 0, 1, 0, 1, exchange="NMS"),
        UnderlyingVolume(3, "SPY", "dup", 1, 0, 1, 0, 1, exchange="PCX"),
    ]
    lines = leaders_to_tradingview_lines(leaders)
    assert lines[0].endswith("SPY")
    assert lines[1].endswith("AAPL")
    assert len(lines) == 2


def test_export_writes_txt(tmp_path: Path):
    result = LeadersResult(
        leaders=[
            UnderlyingVolume(1, "NVDA", "NVIDIA", 100, 1, 60, 40, 5, exchange="NMS"),
            UnderlyingVolume(2, "TSLA", "Tesla", 90, 1, 50, 40, 4, exchange="NMS"),
        ],
        contracts_scanned=10,
        unique_underlyings=2,
        fetched_at=datetime(2026, 7, 14, 12, 0, 0),
    )
    out = tmp_path / "tv.txt"
    path = export_tradingview_watchlist(result, path=out)
    text = path.read_text(encoding="utf-8")
    assert "NASDAQ:NVDA" in text
    assert "NASDAQ:TSLA" in text
    assert text.strip().splitlines()[0].startswith("#")


def test_export_empty_raises():
    with pytest.raises(OptionChainError, match="No leaders"):
        export_tradingview_watchlist(LeadersResult())


def test_cli_export_only_with_top():
    from optionchain.cli import build_parser, _validate_args

    parser = build_parser()
    args = parser.parse_args(["top", "-n", "30", "--export"])
    _validate_args(args)
    assert args.export_path == "__AUTO__"
    assert args.count == 30

    args = parser.parse_args(["TSLA", "--export", "x.txt"])
    with pytest.raises(OptionChainError, match="top command"):
        _validate_args(args)
