"""Tests for call wall / put wall / max pain analysis."""

from __future__ import annotations

import pandas as pd
import pytest

from optionchain.walls import (
    compute_max_pain,
    evaluate_walls,
    find_walls,
    WallLevel,
)


def _calls_puts():
    # Spot assumed 100
    calls = pd.DataFrame(
        {
            "strike": [95.0, 100.0, 105.0, 110.0, 115.0],
            "openInterest": [100, 200, 800, 400, 50],
            "volume": [10, 20, 80, 40, 5],
        }
    )
    puts = pd.DataFrame(
        {
            "strike": [85.0, 90.0, 95.0, 100.0, 105.0],
            "openInterest": [50, 600, 300, 150, 40],
            "volume": [5, 60, 30, 15, 4],
        }
    )
    return calls, puts


def test_find_walls_primary():
    calls, puts = _calls_puts()
    cw, pw, top_c, top_p, tcoi, tpoi = find_walls(calls, puts, spot=100.0, top_n=3)
    assert cw is not None
    assert pw is not None
    # Call wall: max OI at/above spot → 105 with 800
    assert cw.strike == 105.0
    assert cw.open_interest == 800
    # Put wall: max OI at/below spot → 90 with 600
    assert pw.strike == 90.0
    assert pw.open_interest == 600
    assert len(top_c) == 3
    assert top_c[0].strike == 105.0
    assert tcoi == 1550
    assert tpoi == 1140


def test_max_pain_reasonable():
    calls, puts = _calls_puts()
    mp = compute_max_pain(calls, puts)
    assert mp is not None
    assert mp in {85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0}


def test_evaluate_walls_mentions_levels():
    cw = WallLevel(110, 1000, 10, "call", 10.0, 0.4, 1)
    pw = WallLevel(90, 800, 10, "put", -10.0, 0.35, 1)
    notes = evaluate_walls(
        spot=100.0,
        call_wall=cw,
        put_wall=pw,
        max_pain=100.0,
        total_call_oi=2500,
        total_put_oi=2000,
        dte=5,
    )
    text = " ".join(notes).lower()
    assert "call wall" in text
    assert "put wall" in text
    assert "max pain" in text
    assert "research" in text


def test_empty_chain_walls():
    empty = pd.DataFrame()
    cw, pw, tc, tp, _, _ = find_walls(empty, empty, spot=50.0)
    assert cw is None and pw is None
    assert compute_max_pain(empty, empty) is None


def test_cli_walls_flag():
    from optionchain.cli import build_parser, _validate_args
    from optionchain.fetcher import OptionChainError

    parser = build_parser()
    args = parser.parse_args(["TSLA", "--walls", "--top-walls", "3"])
    _validate_args(args)
    assert args.walls is True
    assert args.top_walls == 3

    args = parser.parse_args(["TSLA", "--walls", "--compare", "call"])
    with pytest.raises(OptionChainError):
        _validate_args(args)
