"""Unit tests for ITM/OTM comparison helpers."""

from __future__ import annotations

import pandas as pd
import pytest

from optionchain.compare import (
    build_compare_row,
    break_even_price,
    classify_bucket,
    expiry_value_if_spot,
    filter_compare_rows,
    intrinsic_value,
    moneyness_pct,
    select_style_rows,
    style_guidance,
    CompareResult,
    StrikeCompareRow,
)
from optionchain.fetcher import OptionChainError
from optionchain.cli import build_parser, _validate_args


def test_intrinsic_call_put():
    assert intrinsic_value("call", 100, 90) == 10
    assert intrinsic_value("call", 100, 110) == 0
    assert intrinsic_value("put", 100, 110) == 10
    assert intrinsic_value("put", 100, 90) == 0


def test_moneyness_and_buckets():
    assert moneyness_pct("call", 100, 90) == pytest.approx(10.0)
    assert moneyness_pct("call", 100, 110) == pytest.approx(-10.0)
    assert moneyness_pct("put", 100, 110) == pytest.approx(10.0)
    assert classify_bucket(8.0) == "deep_itm"
    assert classify_bucket(2.0) == "itm"
    assert classify_bucket(0.2) == "atm"
    assert classify_bucket(-2.0) == "otm"
    assert classify_bucket(-8.0) == "far_otm"


def test_break_even_and_expiry_value():
    assert break_even_price("call", 100, 5) == 105
    assert break_even_price("put", 100, 5) == 95
    assert expiry_value_if_spot("call", 100, 120) == 20
    assert expiry_value_if_spot("put", 100, 80) == 20
    assert expiry_value_if_spot("call", 100, 90) == 0


def test_build_compare_row_call():
    raw = pd.Series(
        {
            "strike": 95.0,
            "lastPrice": 7.0,
            "bid": 6.8,
            "ask": 7.2,
            "volume": 100,
            "openInterest": 500,
            "impliedVolatility": 0.3,
            "contractSymbol": "X",
        }
    )
    row = build_compare_row(raw, spot=100.0, option_type="call")
    assert row is not None
    assert row.bucket in {"itm", "deep_itm"}
    assert row.intrinsic == pytest.approx(5.0)
    assert row.extrinsic == pytest.approx(2.0)  # mid 7 - 5
    assert row.break_even == pytest.approx(102.0)
    assert row.premium_per_contract == pytest.approx(700.0)


def test_select_style_rows_picks_buckets():
    rows = []
    for strike, last in [
        (80, 22),
        (95, 7),
        (100, 3),
        (105, 1.5),
        (120, 0.4),
    ]:
        raw = pd.Series(
            {
                "strike": float(strike),
                "lastPrice": float(last),
                "bid": 0.0,
                "ask": 0.0,
                "volume": 50,
                "openInterest": 50,
                "impliedVolatility": 0.25,
                "contractSymbol": f"C{strike}",
            }
        )
        r = build_compare_row(raw, spot=100.0, option_type="call")
        assert r is not None
        rows.append(r)
    picked = select_style_rows(rows, spot=100.0)
    buckets = {p.bucket for p in picked}
    assert "atm" in buckets or "itm" in buckets
    assert len(picked) >= 3


def test_filter_budget_and_target_move():
    rows = [
        StrikeCompareRow(
            strike=100,
            option_type="call",
            last=2,
            bid=0,
            ask=0,
            mid=2,
            volume=10,
            open_interest=10,
            iv=0.2,
            intrinsic=0,
            extrinsic=2,
            break_even=102,
            move_to_be_pct=2.0,
            moneyness_pct=0.0,
            bucket="atm",
            premium_per_contract=200,
            leverage_proxy=50,
        ),
        StrikeCompareRow(
            strike=120,
            option_type="call",
            last=0.5,
            bid=0,
            ask=0,
            mid=0.5,
            volume=10,
            open_interest=10,
            iv=0.2,
            intrinsic=0,
            extrinsic=0.5,
            break_even=120.5,
            move_to_be_pct=20.5,
            moneyness_pct=-20.0,
            bucket="far_otm",
            premium_per_contract=50,
            leverage_proxy=200,
        ),
    ]
    only_budget = filter_compare_rows(rows, budget=100, option_type="call")
    assert len(only_budget) == 1 and only_budget[0].strike == 120

    within_move = filter_compare_rows(rows, target_move_pct=5, option_type="call")
    assert len(within_move) == 1 and within_move[0].strike == 100


def test_style_guidance_mentions_side():
    result = CompareResult(
        symbol="TEST",
        company_name="Test",
        spot_price=100,
        currency="USD",
        expiry="2026-08-01",
        option_type="call",
        dte=20,
        rows=[],
    )
    text = " ".join(style_guidance(result))
    assert "CALL" in text
    assert "Research aid" in text


def test_cli_compare_flags():
    parser = build_parser()
    args = parser.parse_args(
        ["TSLA", "--compare", "call", "--target-move", "5", "--budget", "500"]
    )
    _validate_args(args)
    assert args.compare == "call"
    assert args.target_move == 5
    assert args.budget == 500

    args = parser.parse_args(["TSLA", "--compare", "put", "--history", "5"])
    with pytest.raises(OptionChainError, match="either --compare or --history"):
        _validate_args(args)
