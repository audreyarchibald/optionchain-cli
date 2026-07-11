"""Tests for options volume leaders aggregation (mocked network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from optionchain.fetcher import OptionChainError
from optionchain.leaders import (
    TOP_COMMANDS,
    _aggregate_by_underlying,
    fetch_option_volume_leaders,
)


def test_top_commands_include_aliases():
    assert "top" in TOP_COMMANDS
    assert "leaders" in TOP_COMMANDS
    assert "hot" in TOP_COMMANDS


def test_aggregate_by_underlying_sums_volume():
    contracts = [
        {
            "underlyingSymbol": "TSLA",
            "regularMarketVolume": 100,
            "openInterest": 10,
            "optionsType": "Call",
        },
        {
            "underlyingSymbol": "tsla",
            "regularMarketVolume": 50,
            "openInterest": 5,
            "optionsType": "Put",
        },
        {
            "underlyingSymbol": "AAPL",
            "regularMarketVolume": 200,
            "openInterest": 20,
            "optionsType": "Call",
        },
        {
            "underlyingSymbol": "",
            "regularMarketVolume": 999,
            "openInterest": 1,
            "optionsType": "Call",
        },
    ]
    buckets = _aggregate_by_underlying(contracts)
    assert set(buckets) == {"TSLA", "AAPL"}
    assert buckets["TSLA"]["volume"] == 150
    assert buckets["TSLA"]["call_volume"] == 100
    assert buckets["TSLA"]["put_volume"] == 50
    assert buckets["TSLA"]["contracts"] == 2
    assert buckets["AAPL"]["volume"] == 200


def _mock_response(payload: dict, status: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    resp.text = text
    return resp


def test_fetch_option_volume_leaders_ranks_top_n():
    contracts = []
    for i, (sym, vol, otype) in enumerate(
        [
            ("SPY", 5000, "Call"),
            ("SPY", 3000, "Put"),
            ("NVDA", 4000, "Call"),
            ("TSLA", 1000, "Put"),
            ("AAPL", 2500, "Call"),
            ("AAPL", 500, "Put"),
        ]
    ):
        contracts.append(
            {
                "symbol": f"{sym}{i}",
                "underlyingSymbol": sym,
                "regularMarketVolume": vol,
                "openInterest": vol // 2,
                "optionsType": otype,
                "shortName": f"{sym} option",
            }
        )

    screener_payload = {
        "finance": {
            "result": [
                {
                    "quotes": contracts,
                    "total": len(contracts),
                }
            ]
        }
    }
    quote_payload = {
        "quoteResponse": {
            "result": [
                {
                    "symbol": "SPY",
                    "shortName": "SPDR S&P 500",
                    "regularMarketPrice": 500.0,
                    "regularMarketChangePercent": 0.5,
                },
                {
                    "symbol": "NVDA",
                    "shortName": "NVIDIA",
                    "regularMarketPrice": 120.0,
                    "regularMarketChangePercent": -1.2,
                },
                {
                    "symbol": "AAPL",
                    "shortName": "Apple",
                    "regularMarketPrice": 200.0,
                    "regularMarketChangePercent": 0.1,
                },
            ]
        }
    }

    session = MagicMock()
    session.get.side_effect = [
        _mock_response(screener_payload),
        _mock_response(quote_payload),
    ]

    with (
        patch("optionchain.leaders._http_session", return_value=(session, "mock")),
        patch("optionchain.leaders._yahoo_crumb", return_value="test-crumb"),
        patch(
            "optionchain.leaders._enrich_via_yfinance",
            return_value={},
        ),
    ):
        result = fetch_option_volume_leaders(top_n=3, pages=1)

    assert result.contracts_scanned == 6
    assert result.unique_underlyings == 4
    assert [r.symbol for r in result.leaders] == ["SPY", "NVDA", "AAPL"]
    assert result.leaders[0].options_volume == 8000
    assert result.leaders[0].put_call_ratio == 0.6  # 3000/5000
    assert result.leaders[0].name == "SPDR S&P 500"
    assert result.leaders[0].spot_price == 500.0
    assert result.leaders[1].options_volume == 4000
    assert result.leaders[2].options_volume == 3000
def test_fetch_leaders_rejects_bad_count():
    with pytest.raises(OptionChainError, match="at least 1"):
        fetch_option_volume_leaders(top_n=0)
    with pytest.raises(OptionChainError, match="exceed 100"):
        fetch_option_volume_leaders(top_n=101)


def test_cli_top_validation():
    from optionchain.cli import build_parser, _validate_args, _is_top_command

    assert _is_top_command("top")
    assert _is_top_command("LEADERS")
    assert not _is_top_command("TSLA")

    parser = build_parser()
    args = parser.parse_args(["top", "-n", "15"])
    _validate_args(args)
    assert args.count == 15

    args = parser.parse_args(["top", "-n", "0"])
    with pytest.raises(OptionChainError, match="at least 1"):
        _validate_args(args)
