"""Export option-volume leaders as a TradingView-importable watchlist."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from optionchain.fetcher import OptionChainError
from optionchain.leaders import LeadersResult, UnderlyingVolume

# Yahoo exchange / mic codes → TradingView prefixes (best-effort).
_YAHOO_EXCHANGE_TO_TV: dict[str, str] = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NAS": "NASDAQ",
    "NYQ": "NYSE",
    "NYE": "NYSE",
    "NYS": "NYSE",
    "PCX": "AMEX",
    "ARCA": "AMEX",
    "ASE": "AMEX",
    "AMX": "AMEX",
    "BTS": "BATS",
    "WCB": "NYSE",
    "NIM": "NASDAQ",
    "CXI": "CBOE",
}

# Common index underlyings on Yahoo (^...) → TradingView symbols.
_INDEX_MAP: dict[str, str] = {
    "^VIX": "TVC:VIX",
    "^GSPC": "SP:SPX",
    "^SPX": "SP:SPX",
    "^IXIC": "NASDAQ:IXIC",
    "^DJI": "DJ:DJI",
    "^RUT": "TVC:RUT",
    "^NDX": "NASDAQ:NDX",
}


def yahoo_exchange_to_tv(exchange: str | None) -> str | None:
    """Map a Yahoo exchange code or full name to a TradingView prefix."""
    if not exchange:
        return None
    raw = exchange.strip()
    if not raw:
        return None
    code = raw.upper()
    if code in _YAHOO_EXCHANGE_TO_TV:
        return _YAHOO_EXCHANGE_TO_TV[code]
    # fullExchangeName examples: "NasdaqGS", "NYSE", "NYSEArca"
    lowered = raw.lower()
    if "nasdaq" in lowered:
        return "NASDAQ"
    if "nyse arca" in lowered or "arca" in lowered:
        return "AMEX"
    if "nyse" in lowered:
        return "NYSE"
    if "cboe" in lowered:
        return "CBOE"
    if "amex" in lowered or "nyse mkt" in lowered:
        return "AMEX"
    return None


def to_tradingview_symbol(
    symbol: str,
    *,
    exchange: str | None = None,
    with_exchange: bool = True,
) -> str | None:
    """
    Convert a Yahoo underlying symbol to a TradingView watchlist entry.

    Returns None if the symbol should be skipped (unknown index, etc.).
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return None

    if sym in _INDEX_MAP:
        return _INDEX_MAP[sym]

    if sym.startswith("^"):
        # Unknown index — skip rather than write a broken ticker
        return None

    # Yahoo class shares: BRK-B → BRK.B (TradingView style)
    tv_sym = sym.replace("-", ".")

    if not with_exchange:
        return tv_sym

    prefix = yahoo_exchange_to_tv(exchange)
    if prefix:
        return f"{prefix}:{tv_sym}"
    return tv_sym


def leaders_to_tradingview_lines(
    leaders: list[UnderlyingVolume],
    *,
    with_exchange: bool = True,
) -> list[str]:
    """Build unique TradingView symbols in rank order."""
    lines: list[str] = []
    seen: set[str] = set()
    for row in leaders:
        tv = to_tradingview_symbol(
            row.symbol,
            exchange=getattr(row, "exchange", None),
            with_exchange=with_exchange,
        )
        if not tv or tv in seen:
            continue
        seen.add(tv)
        lines.append(tv)
    return lines


def default_watchlist_path(count: int) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(f"optionchain_top{count}_tradingview_{stamp}.txt")


def export_tradingview_watchlist(
    result: LeadersResult,
    *,
    path: str | Path | None = None,
    with_exchange: bool = True,
    header_comment: bool = True,
) -> Path:
    """
    Write leaders to a ``.txt`` file for TradingView → Watchlist → Import list.

    Format: one symbol per line, optionally ``EXCHANGE:SYMBOL``.
    """
    if not result.leaders:
        raise OptionChainError("No leaders to export.")

    lines = leaders_to_tradingview_lines(
        result.leaders, with_exchange=with_exchange
    )
    if not lines:
        raise OptionChainError(
            "Could not map any leaders to TradingView symbols to export."
        )

    out = Path(path) if path is not None else default_watchlist_path(len(lines))
    out = out.expanduser().resolve()
    if out.suffix.lower() not in {".txt", ".csv"}:
        out = out.with_suffix(".txt")
    out.parent.mkdir(parents=True, exist_ok=True)

    body: list[str] = []
    if header_comment:
        body.append(
            f"# optionchain top {len(lines)} — options volume leaders "
            f"({result.fetched_at.strftime('%Y-%m-%d %H:%M')})"
        )
        body.append(
            "# Import in TradingView: Watchlist → ··· → Import list of symbols"
        )
        body.append("# Lines starting with # are ignored by most importers.")
    body.extend(lines)
    body.append("")  # trailing newline

    out.write_text("\n".join(body), encoding="utf-8")
    return out
