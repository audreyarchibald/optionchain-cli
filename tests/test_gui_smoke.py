"""Smoke tests for GUI helpers (no window interaction)."""

from __future__ import annotations

from optionchain import gui as gui_mod
from optionchain.plotting import build_chain_history_figure
from optionchain.history import (
    ChainHistoryResult,
    ContractDayPoint,
    ContractHistory,
)
from datetime import date


def test_gui_module_exports_main():
    assert callable(gui_mod.main)
    assert callable(gui_mod.run_gui)


def test_err_text_option_chain_error():
    from optionchain.fetcher import OptionChainError

    assert "hello" in gui_mod._err_text(OptionChainError("hello"))


def test_build_figure_for_gui_embed():
    d1, d2 = date(2026, 7, 7), date(2026, 7, 8)
    result = ChainHistoryResult(
        symbol="SPY",
        company_name="SPY",
        spot_price=500.0,
        currency="USD",
        expiry="2026-07-18",
        days_requested=2,
        trade_dates=[d1, d2],
        spot_by_date={d1: 498.0, d2: 500.0},
        contracts=[
            ContractHistory(
                "C1",
                "call",
                500.0,
                "2026-07-18",
                [
                    ContractDayPoint(d1, 4.0, 10),
                    ContractDayPoint(d2, 5.0, 20),
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
                ],
            ),
        ],
    )
    fig = build_chain_history_figure(result, figsize=(6, 4))
    assert fig is not None
    import matplotlib.pyplot as plt

    plt.close(fig)
