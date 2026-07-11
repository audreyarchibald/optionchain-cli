"""Unit tests for multi-day option chain history helpers."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from optionchain.fetcher import OptionChainError
from optionchain.history import (
    ChainHistoryResult,
    ContractDayPoint,
    ContractHistory,
    _period_for_days,
    _union_trade_dates,
    fetch_chain_history,
    summarize_chain_history,
)


def test_period_for_days():
    assert _period_for_days(3) == "10d"
    assert _period_for_days(8) == "1mo"
    assert _period_for_days(20) == "2mo"
    assert _period_for_days(30) == "2mo"
    assert _period_for_days(31) == "3mo"


def test_contract_history_changes():
    c = ContractHistory(
        contract_symbol="TSLA260718C00400",
        option_type="call",
        strike=400.0,
        expiry="2026-07-18",
        points=[
            ContractDayPoint(date(2026, 7, 6), 10.0, 100),
            ContractDayPoint(date(2026, 7, 7), 12.0, 200),
            ContractDayPoint(date(2026, 7, 8), 11.0, 150),
        ],
    )
    assert c.first_close == 10.0
    assert c.last_close == 11.0
    assert c.dollar_change == 1.0
    assert c.percent_change == 10.0
    assert c.close_on(date(2026, 7, 7)) == 12.0
    assert c.volume_on(date(2026, 7, 8)) == 150


def test_union_trade_dates_keeps_last_n():
    contracts = [
        ContractHistory(
            "A",
            "call",
            100,
            "2026-07-18",
            [
                ContractDayPoint(date(2026, 7, d), 1.0, 1)
                for d in range(1, 8)
            ],
        )
    ]
    days = _union_trade_dates(contracts, max_days=3)
    assert days == [date(2026, 7, 5), date(2026, 7, 6), date(2026, 7, 7)]


def test_summarize_chain_history():
    result = ChainHistoryResult(
        symbol="TSLA",
        company_name="Tesla",
        spot_price=400.0,
        currency="USD",
        expiry="2026-07-18",
        days_requested=3,
        trade_dates=[date(2026, 7, 6), date(2026, 7, 8)],
        spot_by_date={date(2026, 7, 6): 390.0, date(2026, 7, 8): 400.0},
        contracts=[
            ContractHistory(
                "C1",
                "call",
                400,
                "2026-07-18",
                [
                    ContractDayPoint(date(2026, 7, 6), 10.0, 1),
                    ContractDayPoint(date(2026, 7, 8), 15.0, 2),
                ],
            ),
            ContractHistory(
                "P1",
                "put",
                400,
                "2026-07-18",
                [
                    ContractDayPoint(date(2026, 7, 6), 10.0, 1),
                    ContractDayPoint(date(2026, 7, 8), 5.0, 2),
                ],
            ),
        ],
    )
    s = summarize_chain_history(result)
    assert s["contracts"] == 2
    assert s["up"] == 1
    assert s["down"] == 1
    assert s["spot_change_pct"] == pytest.approx(2.56, abs=0.02)


def test_fetch_chain_history_rejects_bad_days():
    with pytest.raises(OptionChainError, match="at least 1"):
        fetch_chain_history("TSLA", days=0)
    with pytest.raises(OptionChainError, match="at most 30"):
        fetch_chain_history("TSLA", days=60)


def test_fetch_chain_history_happy_path_mocked():
    from optionchain.fetcher import OptionChainData

    calls = pd.DataFrame(
        {
            "expiry": ["2026-07-18", "2026-07-18"],
            "type": ["call", "call"],
            "strike": [100.0, 105.0],
            "lastPrice": [5.0, 3.0],
            "bid": [0.0, 0.0],
            "ask": [0.0, 0.0],
            "change": [0.0, 0.0],
            "percentChange": [0.0, 0.0],
            "volume": [500, 200],
            "openInterest": [1000, 800],
            "impliedVolatility": [0.3, 0.3],
            "inTheMoney": [True, False],
            "contractSymbol": ["AAA", "BBB"],
        }
    )
    puts = pd.DataFrame(
        {
            "expiry": ["2026-07-18"],
            "type": ["put"],
            "strike": [100.0],
            "lastPrice": [2.0],
            "bid": [0.0],
            "ask": [0.0],
            "change": [0.0],
            "percentChange": [0.0],
            "volume": [100],
            "openInterest": [400],
            "impliedVolatility": [0.3],
            "inTheMoney": [False],
            "contractSymbol": ["CCC"],
        }
    )
    data = OptionChainData(
        symbol="TEST",
        company_name="Test Inc",
        spot_price=102.0,
        currency="USD",
        expiries=["2026-07-18"],
        calls=calls,
        puts=puts,
    )

    def fake_contract_hist(csym, otype, strike, expiry, period, max_days):
        return ContractHistory(
            csym,
            otype,
            strike,
            expiry,
            [
                ContractDayPoint(date(2026, 7, 7), 4.0, 10),
                ContractDayPoint(date(2026, 7, 8), 5.0, 20),
                ContractDayPoint(date(2026, 7, 9), 6.0, 30),
            ][:max_days],
        )

    spot_idx = pd.Index([date(2026, 7, 7), date(2026, 7, 8), date(2026, 7, 9)])
    spot_hist = pd.DataFrame({"Close": [100.0, 101.0, 102.0]}, index=spot_idx)

    with (
        patch(
            "optionchain.history.list_expiries",
            return_value=("TEST", "Test Inc", 102.0, ["2026-07-18"]),
        ),
        patch("optionchain.history.fetch_option_chain", return_value=data),
        patch(
            "optionchain.history._fetch_contract_history",
            side_effect=fake_contract_hist,
        ),
        patch("optionchain.history._history_frame", return_value=spot_hist),
    ):
        result = fetch_chain_history("TEST", days=3, near=5, option_type="all")

    assert result.symbol == "TEST"
    assert result.expiry == "2026-07-18"
    assert len(result.contracts) >= 1
    assert len(result.trade_dates) == 3
    assert result.spot_by_date[date(2026, 7, 9)] == 102.0


def test_cli_history_flag():
    from optionchain.cli import build_parser, _validate_args

    parser = build_parser()
    args = parser.parse_args(["TSLA", "--history", "5", "--type", "call"])
    _validate_args(args)
    assert args.history_days == 5

    args = parser.parse_args(["TSLA", "--history", "0"])
    with pytest.raises(OptionChainError, match="at least 1"):
        _validate_args(args)

    args = parser.parse_args(
        ["TSLA", "--history", "5", "--from", "2026-07-01", "--to", "2026-08-01"]
    )
    with pytest.raises(OptionChainError, match="single expiry"):
        _validate_args(args)
