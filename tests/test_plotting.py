"""Tests for call/put history plotting (terminal + optional PNG save)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from optionchain.fetcher import OptionChainError
from optionchain.history import (
    ChainHistoryResult,
    ContractDayPoint,
    ContractHistory,
)
from optionchain.plotting import (
    plot_chain_history,
    print_terminal_plot,
    save_chain_history_plot,
)


def _sample_result() -> ChainHistoryResult:
    d1, d2, d3 = date(2026, 7, 7), date(2026, 7, 8), date(2026, 7, 9)
    return ChainHistoryResult(
        symbol="SPY",
        company_name="SPDR S&P 500",
        spot_price=500.0,
        currency="USD",
        expiry="2026-07-18",
        days_requested=3,
        trade_dates=[d1, d2, d3],
        spot_by_date={d1: 495.0, d2: 498.0, d3: 500.0},
        contracts=[
            ContractHistory(
                "C1",
                "call",
                500.0,
                "2026-07-18",
                [
                    ContractDayPoint(d1, 4.0, 10),
                    ContractDayPoint(d2, 5.0, 20),
                    ContractDayPoint(d3, 6.0, 30),
                ],
            ),
            ContractHistory(
                "C2",
                "call",
                505.0,
                "2026-07-18",
                [
                    ContractDayPoint(d1, 2.0, 5),
                    ContractDayPoint(d2, 2.5, 6),
                    ContractDayPoint(d3, 3.0, 7),
                ],
            ),
            ContractHistory(
                "P1",
                "put",
                500.0,
                "2026-07-18",
                [
                    ContractDayPoint(d1, 5.0, 8),
                    ContractDayPoint(d2, 4.0, 9),
                    ContractDayPoint(d3, 3.0, 10),
                ],
            ),
        ],
    )


def test_save_chain_history_plot_writes_png(tmp_path: Path):
    out = tmp_path / "spy_hist.png"
    path = save_chain_history_plot(_sample_result(), path=out)
    assert path == out.resolve()
    assert path.exists()
    assert path.stat().st_size > 1000


def test_print_terminal_plot_runs(capsys):
    print_terminal_plot(_sample_result())
    captured = capsys.readouterr().out
    assert "CALLS" in captured or "calls" in captured.lower() or "SPY" in captured
    assert "PUTS" in captured or "puts" in captured.lower() or "Underlying" in captured


def test_plot_empty_raises():
    result = _sample_result()
    result.contracts = []
    with pytest.raises(OptionChainError, match="No contracts"):
        save_chain_history_plot(result)


def test_plot_chain_history_terminal_only_no_file(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("optionchain.plotting.print_terminal_plot") as term:
        path = plot_chain_history(_sample_result(), terminal=True, path=None)
    term.assert_called_once()
    assert path is None
    assert list(tmp_path.glob("*.png")) == []


def test_plot_chain_history_with_path_saves(tmp_path: Path):
    out = tmp_path / "out.png"
    with patch("optionchain.plotting.print_terminal_plot"):
        path = plot_chain_history(_sample_result(), path=out, terminal=True)
    assert path is not None and path.exists()


def test_cli_save_and_plot_flags():
    from optionchain.cli import build_parser, _validate_args

    parser = build_parser()
    args = parser.parse_args(["TSLA", "--plot"])
    with pytest.raises(OptionChainError, match="--history"):
        _validate_args(args)

    args = parser.parse_args(["TSLA", "--history", "5", "--plot"])
    _validate_args(args)
    assert args.plot is True
    assert args.save_path is None

    args = parser.parse_args(["TSLA", "--history", "5", "--save"])
    _validate_args(args)
    assert args.plot is True
    assert args.save_path == "__AUTO__"

    args = parser.parse_args(
        ["TSLA", "--history", "5", "--save", "/tmp/chart.png"]
    )
    _validate_args(args)
    assert args.save_path == "/tmp/chart.png"
