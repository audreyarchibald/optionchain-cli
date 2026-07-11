"""Unit tests for expiry/type/strike filters."""

from __future__ import annotations

import pandas as pd
import pytest

from optionchain.fetcher import OptionChainError
from optionchain.filters import (
    apply_filters,
    filter_by_strike,
    filter_by_type,
    select_expiries,
)


AVAILABLE = [
    "2026-07-11",
    "2026-07-18",
    "2026-07-25",
    "2026-08-15",
    "2026-09-19",
    "2026-12-18",
]


def _sample_frames():
    calls = pd.DataFrame(
        {
            "expiry": ["2026-07-18"] * 5,
            "type": ["call"] * 5,
            "strike": [100.0, 105.0, 110.0, 115.0, 120.0],
            "lastPrice": [12.0, 8.0, 5.0, 3.0, 1.5],
            "bid": [11.5, 7.5, 4.5, 2.5, 1.2],
            "ask": [12.5, 8.5, 5.5, 3.5, 1.8],
            "change": [0.0] * 5,
            "percentChange": [0.0] * 5,
            "volume": [100, 200, 300, 150, 50],
            "openInterest": [1000, 2000, 3000, 1500, 500],
            "impliedVolatility": [0.3, 0.32, 0.35, 0.4, 0.45],
            "inTheMoney": [True, True, False, False, False],
            "contractSymbol": [f"C{i}" for i in range(5)],
        }
    )
    puts = pd.DataFrame(
        {
            "expiry": ["2026-07-18"] * 5,
            "type": ["put"] * 5,
            "strike": [100.0, 105.0, 110.0, 115.0, 120.0],
            "lastPrice": [1.0, 2.0, 4.0, 7.0, 11.0],
            "bid": [0.8, 1.8, 3.5, 6.5, 10.5],
            "ask": [1.2, 2.2, 4.5, 7.5, 11.5],
            "change": [0.0] * 5,
            "percentChange": [0.0] * 5,
            "volume": [80, 160, 240, 120, 40],
            "openInterest": [800, 1600, 2400, 1200, 400],
            "impliedVolatility": [0.31, 0.33, 0.36, 0.41, 0.46],
            "inTheMoney": [False, False, True, True, True],
            "contractSymbol": [f"P{i}" for i in range(5)],
        }
    )
    return calls, puts


def test_select_default_nearest_one():
    assert select_expiries(AVAILABLE) == ["2026-07-11"]


def test_select_nearest_three():
    assert select_expiries(AVAILABLE, nearest=3) == AVAILABLE[:3]


def test_select_exact_expiry():
    assert select_expiries(AVAILABLE, expiry="2026-08-15") == ["2026-08-15"]


def test_select_exact_expiry_missing():
    with pytest.raises(OptionChainError, match="not listed"):
        select_expiries(AVAILABLE, expiry="2099-01-01")


def test_select_date_range():
    selected = select_expiries(
        AVAILABLE, expiry_from="2026-07-15", expiry_to="2026-08-31"
    )
    assert selected == ["2026-07-18", "2026-07-25", "2026-08-15"]


def test_select_date_range_empty():
    with pytest.raises(OptionChainError, match="No expiries fall"):
        select_expiries(
            AVAILABLE, expiry_from="2030-01-01", expiry_to="2030-02-01"
        )


def test_select_inverted_range():
    with pytest.raises(OptionChainError, match="after end date"):
        select_expiries(
            AVAILABLE, expiry_from="2026-12-01", expiry_to="2026-07-01"
        )


def test_select_bad_date_format():
    with pytest.raises(OptionChainError, match="YYYY-MM-DD"):
        select_expiries(AVAILABLE, expiry="07/18/2026")


def test_select_nearest_invalid():
    with pytest.raises(OptionChainError, match="at least 1"):
        select_expiries(AVAILABLE, nearest=0)


def test_filter_by_type_call_only():
    calls, puts = _sample_frames()
    out = filter_by_type(calls, puts, "call")
    assert len(out) == 5
    assert set(out["type"]) == {"call"}


def test_filter_by_type_put_only():
    calls, puts = _sample_frames()
    out = filter_by_type(calls, puts, "put")
    assert len(out) == 5
    assert set(out["type"]) == {"put"}


def test_filter_by_type_all():
    calls, puts = _sample_frames()
    out = filter_by_type(calls, puts, "all")
    assert len(out) == 10


def test_filter_by_type_invalid():
    calls, puts = _sample_frames()
    with pytest.raises(OptionChainError, match="Unknown option type"):
        filter_by_type(calls, puts, "butterfly")


def test_filter_strike_range():
    calls, _ = _sample_frames()
    out = filter_by_strike(calls, strike_min=105, strike_max=115)
    assert list(out["strike"]) == [105.0, 110.0, 115.0]


def test_filter_near_atm():
    calls, _ = _sample_frames()
    out = filter_by_strike(calls, spot_price=110.0, near=3)
    assert len(out) == 3
    assert set(out["strike"]) == {105.0, 110.0, 115.0}


def test_apply_filters_combined():
    calls, puts = _sample_frames()
    out = apply_filters(
        calls,
        puts,
        option_type="put",
        strike_min=100,
        strike_max=110,
        spot_price=110,
        near=2,
    )
    assert set(out["type"]) == {"put"}
    assert len(out) == 2
    assert out["strike"].max() <= 110
