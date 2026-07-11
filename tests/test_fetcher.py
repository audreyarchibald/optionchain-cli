"""Unit tests for symbol normalization and date parsing (no network)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from optionchain.fetcher import (
    OptionChainError,
    _normalize_symbol,
    _standardize_chain,
    parse_date,
)


def test_normalize_symbol_ok():
    assert _normalize_symbol("tsla") == "TSLA"
    assert _normalize_symbol("  aapl  ") == "AAPL"
    assert _normalize_symbol("BRK-B") == "BRK-B"


def test_normalize_symbol_empty():
    with pytest.raises(OptionChainError, match="ticker symbol"):
        _normalize_symbol("")
    with pytest.raises(OptionChainError, match="ticker symbol"):
        _normalize_symbol("   ")


def test_normalize_symbol_invalid_chars():
    with pytest.raises(OptionChainError, match="does not look like"):
        _normalize_symbol("TSLA!!!")


def test_parse_date_ok():
    assert parse_date("2026-07-18") == date(2026, 7, 18)


def test_parse_date_bad():
    with pytest.raises(OptionChainError, match="YYYY-MM-DD"):
        parse_date("18-07-2026")


def test_standardize_chain_empty():
    out = _standardize_chain(pd.DataFrame(), "call", "2026-07-18")
    assert out.empty
    assert "strike" in out.columns
    assert "type" in out.columns


def test_standardize_chain_partial_columns():
    raw = pd.DataFrame({"strike": [100, 110], "lastPrice": [5.0, 2.0]})
    out = _standardize_chain(raw, "put", "2026-08-15")
    assert list(out["type"]) == ["put", "put"]
    assert list(out["expiry"]) == ["2026-08-15", "2026-08-15"]
    assert out["volume"].tolist() == [0, 0]
    assert out["openInterest"].tolist() == [0, 0]
