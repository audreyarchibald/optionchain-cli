"""Fetch option chain data for a stock symbol via Yahoo Finance (yfinance)."""

from __future__ import annotations

import logging
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterator

import pandas as pd
import yfinance as yf


class OptionChainError(Exception):
    """Raised when option chain data cannot be retrieved or parsed."""


@dataclass
class OptionChainData:
    """Container for a stock's option chain and market context."""

    symbol: str
    company_name: str
    spot_price: float
    currency: str
    expiries: list[str]
    calls: pd.DataFrame
    puts: pd.DataFrame
    fetched_at: datetime = field(default_factory=datetime.now)

    @property
    def has_options(self) -> bool:
        return bool(self.expiries) and (
            not self.calls.empty or not self.puts.empty
        )


@contextmanager
def _quiet_yfinance() -> Iterator[None]:
    """Suppress noisy yfinance / urllib HTTP logs during lookups."""
    loggers = [
        logging.getLogger("yfinance"),
        logging.getLogger("peewee"),
        logging.getLogger("urllib3"),
    ]
    previous = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.CRITICAL)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            yield
        finally:
            for lg, level in zip(loggers, previous):
                lg.setLevel(level)


def _normalize_symbol(symbol: str) -> str:
    cleaned = (symbol or "").strip().upper()
    if not cleaned:
        raise OptionChainError(
            "Please enter a stock ticker symbol (for example: TSLA, AAPL, SPY)."
        )
    # Allow common Yahoo formats like BRK-B
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-^")
    if any(ch not in allowed for ch in cleaned):
        raise OptionChainError(
            f"'{symbol}' does not look like a valid ticker. "
            "Use letters/numbers only (example: TSLA or BRK-B)."
        )
    return cleaned


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _pick_spot_price(ticker: yf.Ticker, info: dict[str, Any]) -> float:
    for key in ("regularMarketPrice", "currentPrice", "previousClose", "open"):
        price = _to_float(info.get(key), default=0.0)
        if price > 0:
            return price

    try:
        hist = ticker.history(period="5d")
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0


def _looks_like_unknown_ticker(info: dict[str, Any], spot_price: float) -> bool:
    """Heuristic: Yahoo returns nearly-empty info dicts for unknown symbols."""
    if spot_price > 0:
        return False
    meaningful = any(
        info.get(k)
        for k in (
            "shortName",
            "longName",
            "symbol",
            "exchange",
            "quoteType",
            "regularMarketPrice",
            "currentPrice",
            "previousClose",
        )
    )
    return not meaningful


def _standardize_chain(df: pd.DataFrame, option_type: str, expiry: str) -> pd.DataFrame:
    """Normalize a yfinance options DataFrame into a consistent schema."""
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "expiry",
                "type",
                "strike",
                "lastPrice",
                "bid",
                "ask",
                "change",
                "percentChange",
                "volume",
                "openInterest",
                "impliedVolatility",
                "inTheMoney",
                "contractSymbol",
            ]
        )

    out = df.copy()
    out["expiry"] = expiry
    out["type"] = option_type

    for col, default in (
        ("strike", 0.0),
        ("lastPrice", 0.0),
        ("bid", 0.0),
        ("ask", 0.0),
        ("change", 0.0),
        ("percentChange", 0.0),
        ("volume", 0),
        ("openInterest", 0),
        ("impliedVolatility", 0.0),
        ("inTheMoney", False),
        ("contractSymbol", ""),
    ):
        if col not in out.columns:
            out[col] = default

    out["strike"] = pd.to_numeric(out["strike"], errors="coerce").fillna(0.0)
    out["lastPrice"] = pd.to_numeric(out["lastPrice"], errors="coerce").fillna(0.0)
    out["bid"] = pd.to_numeric(out["bid"], errors="coerce").fillna(0.0)
    out["ask"] = pd.to_numeric(out["ask"], errors="coerce").fillna(0.0)
    out["change"] = pd.to_numeric(out["change"], errors="coerce").fillna(0.0)
    out["percentChange"] = pd.to_numeric(out["percentChange"], errors="coerce").fillna(
        0.0
    )
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).astype(int)
    out["openInterest"] = (
        pd.to_numeric(out["openInterest"], errors="coerce").fillna(0).astype(int)
    )
    out["impliedVolatility"] = pd.to_numeric(
        out["impliedVolatility"], errors="coerce"
    ).fillna(0.0)
    # Yahoo sometimes returns a tiny placeholder (1e-5) instead of real IV
    out.loc[out["impliedVolatility"] < 1e-4, "impliedVolatility"] = 0.0
    out["inTheMoney"] = out["inTheMoney"].fillna(False).astype(bool)

    cols = [
        "expiry",
        "type",
        "strike",
        "lastPrice",
        "bid",
        "ask",
        "change",
        "percentChange",
        "volume",
        "openInterest",
        "impliedVolatility",
        "inTheMoney",
        "contractSymbol",
    ]
    return out[cols].sort_values("strike").reset_index(drop=True)


def parse_date(value: str, label: str = "date") -> date:
    """Parse YYYY-MM-DD into a date. Raises OptionChainError on bad input."""
    text = (value or "").strip()
    if not text:
        raise OptionChainError(f"Please provide a {label} in YYYY-MM-DD format.")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise OptionChainError(
            f"Invalid {label} '{value}'. Use YYYY-MM-DD (example: 2026-08-15)."
        ) from exc


def _load_ticker_meta(symbol: str) -> tuple[yf.Ticker, dict[str, Any], str, float, str]:
    ticker_symbol = _normalize_symbol(symbol)
    with _quiet_yfinance():
        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info or {}
        except Exception as exc:
            raise OptionChainError(
                f"Could not look up '{ticker_symbol}'. Check the ticker and try again."
            ) from exc

        company_name = (
            info.get("shortName")
            or info.get("longName")
            or info.get("symbol")
            or ticker_symbol
        )
        currency = info.get("currency") or "USD"
        spot_price = _pick_spot_price(ticker, info)

        if _looks_like_unknown_ticker(info, spot_price):
            raise OptionChainError(
                f"No market data found for '{ticker_symbol}'.\n"
                "Double-check the ticker spelling.\n"
                "  ✓ Good examples: TSLA, AAPL, SPY, MSFT, NVDA\n"
                "  ✗ Company names won't work: use TSLA not TESLA"
            )

    return ticker, info, str(company_name), float(spot_price), str(currency)


def _load_available_expiries(ticker: yf.Ticker, symbol: str, company_name: str) -> list[str]:
    with _quiet_yfinance():
        try:
            available = list(ticker.options or [])
        except Exception as exc:
            raise OptionChainError(
                f"Could not load option expiries for {symbol}: {exc}"
            ) from exc

    if not available:
        raise OptionChainError(
            f"{symbol} ({company_name}) does not appear to have listed options.\n"
            "Try a liquid stock or ETF such as AAPL, SPY, QQQ, or TSLA."
        )
    return available


def fetch_option_chain(
    symbol: str,
    expiries: list[str] | None = None,
) -> OptionChainData:
    """
    Download option chain data for ``symbol``.

    Parameters
    ----------
    symbol:
        Stock ticker (e.g. TSLA).
    expiries:
        Optional list of expiry dates (YYYY-MM-DD) to fetch.
        If None, all available expiries are fetched.
    """
    ticker, _info, company_name, spot_price, currency = _load_ticker_meta(symbol)
    ticker_symbol = _normalize_symbol(symbol)
    available = _load_available_expiries(ticker, ticker_symbol, company_name)

    if expiries is None:
        selected = available
    else:
        missing = [e for e in expiries if e not in available]
        if missing:
            sample = ", ".join(available[:5])
            more = f" (+{len(available) - 5} more)" if len(available) > 5 else ""
            raise OptionChainError(
                f"Expiry date(s) not available for {ticker_symbol}: {', '.join(missing)}.\n"
                f"Available expiries include: {sample}{more}\n"
                f"Tip: run  optionchain {ticker_symbol} --list-expiries  to see all dates."
            )
        selected = expiries

    call_frames: list[pd.DataFrame] = []
    put_frames: list[pd.DataFrame] = []

    with _quiet_yfinance():
        for expiry in selected:
            try:
                chain = ticker.option_chain(expiry)
            except Exception as exc:
                raise OptionChainError(
                    f"Failed to download the option chain for {ticker_symbol} "
                    f"expiring {expiry}: {exc}"
                ) from exc
            call_frames.append(_standardize_chain(chain.calls, "call", expiry))
            put_frames.append(_standardize_chain(chain.puts, "put", expiry))

    calls = (
        pd.concat(call_frames, ignore_index=True)
        if call_frames
        else _standardize_chain(pd.DataFrame(), "call", "")
    )
    puts = (
        pd.concat(put_frames, ignore_index=True)
        if put_frames
        else _standardize_chain(pd.DataFrame(), "put", "")
    )

    if spot_price <= 0:
        strikes = pd.concat([calls["strike"], puts["strike"]], ignore_index=True)
        if not strikes.empty:
            spot_price = float(strikes.median())

    return OptionChainData(
        symbol=ticker_symbol,
        company_name=company_name,
        spot_price=float(spot_price),
        currency=currency,
        expiries=list(available),
        calls=calls,
        puts=puts,
    )


def list_expiries(symbol: str) -> tuple[str, str, float, list[str]]:
    """Return (symbol, company_name, spot_price, available_expiries)."""
    ticker, _info, company_name, spot_price, _currency = _load_ticker_meta(symbol)
    ticker_symbol = _normalize_symbol(symbol)
    available = _load_available_expiries(ticker, ticker_symbol, company_name)
    return ticker_symbol, company_name, float(spot_price), available
