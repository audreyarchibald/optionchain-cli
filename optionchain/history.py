"""Multi-day option-chain change tracking via Yahoo Finance."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd
import yfinance as yf

from optionchain.fetcher import (
    OptionChainData,
    OptionChainError,
    _quiet_yfinance,
    fetch_option_chain,
    list_expiries,
)
from optionchain.filters import apply_filters, select_expiries


@dataclass
class ContractDayPoint:
    """One trading day's close + volume for a single option contract."""

    trade_date: date
    close: float
    volume: int


@dataclass
class ContractHistory:
    """Price path for one option contract over several sessions."""

    contract_symbol: str
    option_type: str  # call | put
    strike: float
    expiry: str
    points: list[ContractDayPoint] = field(default_factory=list)

    @property
    def first_close(self) -> float | None:
        return self.points[0].close if self.points else None

    @property
    def last_close(self) -> float | None:
        return self.points[-1].close if self.points else None

    @property
    def dollar_change(self) -> float | None:
        if self.first_close is None or self.last_close is None:
            return None
        return round(self.last_close - self.first_close, 4)

    @property
    def percent_change(self) -> float | None:
        if not self.first_close or self.last_close is None:
            return None
        return round(100.0 * (self.last_close - self.first_close) / self.first_close, 2)

    def close_on(self, day: date) -> float | None:
        for p in self.points:
            if p.trade_date == day:
                return p.close
        return None

    def volume_on(self, day: date) -> int | None:
        for p in self.points:
            if p.trade_date == day:
                return p.volume
        return None


@dataclass
class ChainHistoryResult:
    """Option chain multi-day change snapshot for one underlying."""

    symbol: str
    company_name: str
    spot_price: float
    currency: str
    expiry: str
    days_requested: int
    trade_dates: list[date]
    spot_by_date: dict[date, float]
    contracts: list[ContractHistory]
    fetched_at: datetime = field(default_factory=datetime.now)

    @property
    def spot_change(self) -> float | None:
        if len(self.trade_dates) < 2:
            return None
        first = self.spot_by_date.get(self.trade_dates[0])
        last = self.spot_by_date.get(self.trade_dates[-1])
        if first is None or last is None:
            return None
        return round(last - first, 4)

    @property
    def spot_change_pct(self) -> float | None:
        if len(self.trade_dates) < 2:
            return None
        first = self.spot_by_date.get(self.trade_dates[0])
        last = self.spot_by_date.get(self.trade_dates[-1])
        if not first or last is None:
            return None
        return round(100.0 * (last - first) / first, 2)


def _period_for_days(days: int) -> str:
    """Map requested lookback to a yfinance period string (with buffer)."""
    if days <= 5:
        return "10d"
    if days <= 10:
        return "1mo"
    if days <= 30:
        return "2mo"
    return "3mo"


def _history_frame(ticker_symbol: str, period: str) -> pd.DataFrame:
    with _quiet_yfinance():
        try:
            hist = yf.Ticker(ticker_symbol).history(period=period, auto_adjust=True)
        except Exception:
            return pd.DataFrame()
    if hist is None or hist.empty:
        return pd.DataFrame()
    out = hist.copy()
    # Normalize index to bare dates
    try:
        out.index = pd.to_datetime(out.index).tz_localize(None).date
    except (TypeError, AttributeError, ValueError):
        out.index = pd.to_datetime(out.index).date
    return out


def _fetch_contract_history(
    contract_symbol: str,
    option_type: str,
    strike: float,
    expiry: str,
    period: str,
    max_days: int,
) -> ContractHistory:
    hist = _history_frame(contract_symbol, period)
    points: list[ContractDayPoint] = []
    if not hist.empty and "Close" in hist.columns:
        # Keep last max_days sessions with a real close
        rows = hist.dropna(subset=["Close"]).tail(max_days)
        for idx, row in rows.iterrows():
            day = idx if isinstance(idx, date) else pd.Timestamp(idx).date()
            vol = int(row["Volume"]) if "Volume" in row and pd.notna(row["Volume"]) else 0
            points.append(
                ContractDayPoint(
                    trade_date=day,
                    close=float(row["Close"]),
                    volume=vol,
                )
            )
    return ContractHistory(
        contract_symbol=contract_symbol,
        option_type=option_type,
        strike=float(strike),
        expiry=expiry,
        points=points,
    )


def _union_trade_dates(contracts: list[ContractHistory], max_days: int) -> list[date]:
    days: set[date] = set()
    for c in contracts:
        for p in c.points:
            days.add(p.trade_date)
    ordered = sorted(days)
    if len(ordered) > max_days:
        ordered = ordered[-max_days:]
    return ordered


def fetch_chain_history(
    symbol: str,
    *,
    days: int = 5,
    expiry: str | None = None,
    option_type: str = "all",
    near: int = 6,
    strike_min: float | None = None,
    strike_max: float | None = None,
    max_contracts: int = 24,
) -> ChainHistoryResult:
    """
    Show how near-the-money option contracts for ``symbol`` moved over recent days.

    Pulls the live chain, keeps a focused set of strikes, then downloads each
    contract's daily close/volume history from Yahoo Finance.

    Parameters
    ----------
    symbol:
        Stock ticker (e.g. TSLA).
    days:
        Number of recent trading sessions to display (1–30).
    expiry:
        Optional YYYY-MM-DD. Defaults to the nearest listed expiry.
    option_type:
        ``call``, ``put``, or ``all``.
    near:
        How many strikes around spot to include (per type).
    max_contracts:
        Hard cap on contracts fetched (history calls are slow).
    """
    if days < 1:
        raise OptionChainError("--history days must be at least 1.")
    if days > 30:
        raise OptionChainError(
            "--history supports at most 30 trading days "
            "(Yahoo option history gets sparse beyond that)."
        )
    if near is not None and near < 1:
        raise OptionChainError("--near must be at least 1 when used with --history.")
    if max_contracts < 1:
        raise OptionChainError("max_contracts must be at least 1.")

    ticker_symbol, company_name, spot, available = list_expiries(symbol)
    selected = select_expiries(available, expiry=expiry, nearest=1)
    data: OptionChainData = fetch_option_chain(ticker_symbol, expiries=selected)
    if data.spot_price > 0:
        spot = data.spot_price

    filtered = apply_filters(
        data.calls,
        data.puts,
        option_type=option_type,
        strike_min=strike_min,
        strike_max=strike_max,
        spot_price=spot,
        near=near,
    )
    if filtered.empty:
        raise OptionChainError(
            "No option contracts matched your filters for the history view.\n"
            "Try --type all, a larger --near, or another --expiry."
        )

    # Prefer highest volume so sparse/dead strikes don't waste slots
    ranked = filtered.copy()
    if "volume" in ranked.columns:
        ranked = ranked.sort_values("volume", ascending=False)
    ranked = ranked.head(max_contracts)

    period = _period_for_days(days)
    jobs: list[tuple[str, str, float, str]] = []
    for _, row in ranked.iterrows():
        csym = str(row.get("contractSymbol") or "").strip()
        if not csym:
            continue
        jobs.append(
            (
                csym,
                str(row.get("type") or "").lower(),
                float(row.get("strike") or 0),
                str(row.get("expiry") or selected[0]),
            )
        )

    if not jobs:
        raise OptionChainError(
            "Could not find contract symbols on this chain to download history."
        )

    contracts: list[ContractHistory] = []
    # Parallelize — each contract is an independent Yahoo request
    workers = min(8, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _fetch_contract_history, csym, otype, strike, exp, period, days
            ): csym
            for csym, otype, strike, exp in jobs
        }
        for fut in as_completed(futures):
            try:
                contracts.append(fut.result())
            except Exception:
                continue

    contracts.sort(key=lambda c: (c.expiry, c.option_type, c.strike))

    # Drop contracts with no history at all
    with_data = [c for c in contracts if c.points]
    if not with_data:
        raise OptionChainError(
            f"Yahoo returned no multi-day history for {ticker_symbol} options.\n"
            "This is common for illiquid strikes or brand-new weeklies. "
            "Try a more liquid name (SPY, QQQ) or a nearer expiry."
        )

    trade_dates = _union_trade_dates(with_data, days)

    # Underlying spot path aligned to those sessions
    spot_hist = _history_frame(ticker_symbol, period)
    spot_by_date: dict[date, float] = {}
    if not spot_hist.empty and "Close" in spot_hist.columns:
        for d in trade_dates:
            if d in spot_hist.index:
                spot_by_date[d] = float(spot_hist.loc[d, "Close"])
            else:
                # nearest prior close
                prior = spot_hist[spot_hist.index <= d]
                if not prior.empty:
                    spot_by_date[d] = float(prior["Close"].iloc[-1])
    if spot > 0 and trade_dates:
        spot_by_date.setdefault(trade_dates[-1], float(spot))

    return ChainHistoryResult(
        symbol=ticker_symbol,
        company_name=company_name,
        spot_price=float(spot),
        currency=data.currency,
        expiry=selected[0],
        days_requested=days,
        trade_dates=trade_dates,
        spot_by_date=spot_by_date,
        contracts=with_data,
    )


def summarize_chain_history(result: ChainHistoryResult) -> dict[str, Any]:
    """Quick aggregate stats for the history result."""
    ups = downs = flat = 0
    for c in result.contracts:
        chg = c.percent_change
        if chg is None:
            continue
        if chg > 0.5:
            ups += 1
        elif chg < -0.5:
            downs += 1
        else:
            flat += 1
    return {
        "contracts": len(result.contracts),
        "sessions": len(result.trade_dates),
        "up": ups,
        "down": downs,
        "flat": flat,
        "spot_change_pct": result.spot_change_pct,
    }
