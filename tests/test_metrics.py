"""Unit tests for put/call ratio and summary metrics."""

from __future__ import annotations

import pandas as pd

from optionchain.metrics import compute_put_call_ratio, summarize_chain


def test_put_call_ratio_basic():
    calls = pd.DataFrame({"volume": [100, 100], "openInterest": [500, 500]})
    puts = pd.DataFrame({"volume": [150, 50], "openInterest": [400, 200]})
    pcr = compute_put_call_ratio(calls, puts)
    assert pcr.call_volume == 200
    assert pcr.put_volume == 200
    assert pcr.volume_ratio == 1.0
    assert pcr.call_open_interest == 1000
    assert pcr.put_open_interest == 600
    assert pcr.oi_ratio == 0.6


def test_put_call_ratio_no_call_volume():
    calls = pd.DataFrame({"volume": [0], "openInterest": [0]})
    puts = pd.DataFrame({"volume": [10], "openInterest": [20]})
    pcr = compute_put_call_ratio(calls, puts)
    assert pcr.volume_ratio is None
    assert pcr.oi_ratio is None
    assert "Not enough" in pcr.volume_interpretation


def test_put_call_ratio_ignores_tiny_denominator():
    # Below activity thresholds → ratio withheld
    calls = pd.DataFrame({"volume": [5], "openInterest": [20]})
    puts = pd.DataFrame({"volume": [10], "openInterest": [40]})
    pcr = compute_put_call_ratio(calls, puts)
    assert pcr.volume_ratio is None
    assert pcr.oi_ratio is None


def test_put_call_ratio_empty_frames():
    pcr = compute_put_call_ratio(pd.DataFrame(), pd.DataFrame())
    assert pcr.call_volume == 0
    assert pcr.put_volume == 0
    assert pcr.volume_ratio is None


def test_interpretations_cover_bands():
    calls = pd.DataFrame({"volume": [100], "openInterest": [100]})
    # bullish-leaning
    puts = pd.DataFrame({"volume": [50], "openInterest": [50]})
    assert "bullish" in compute_put_call_ratio(calls, puts).volume_interpretation.lower()
    # balanced
    puts = pd.DataFrame({"volume": [90], "openInterest": [90]})
    assert "balanced" in compute_put_call_ratio(calls, puts).volume_interpretation.lower()
    # high PCR
    puts = pd.DataFrame({"volume": [200], "openInterest": [200]})
    text = compute_put_call_ratio(calls, puts).volume_interpretation.lower()
    assert "more puts" in text or "bearish" in text


def test_summarize_chain():
    df = pd.DataFrame(
        {
            "expiry": ["2026-07-18", "2026-07-18", "2026-08-15"],
            "type": ["call", "put", "call"],
            "strike": [100.0, 100.0, 105.0],
            "volume": [10, 20, 30],
            "openInterest": [100, 200, 300],
            "inTheMoney": [True, False, False],
        }
    )
    s = summarize_chain(df, spot_price=100.0)
    assert s["rows"] == 3
    assert s["expiries"] == 2
    assert s["strikes"] == 2
    assert s["itm_count"] == 1
    assert s["otm_count"] == 2
    assert s["total_volume"] == 60
    assert s["total_oi"] == 600


def test_summarize_empty():
    s = summarize_chain(pd.DataFrame(), spot_price=0)
    assert s["rows"] == 0
