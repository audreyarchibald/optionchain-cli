"""Most-active option underlyings ranked by trading volume."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from optionchain.fetcher import OptionChainError, _quiet_yfinance

YAHOO_SCREENER = (
    "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
)
YAHOO_QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote"

# Pull pages of the most-active option *contracts*, then roll up by stock.
# Yahoo ranks individual contracts; summing them approximates which underlyings
# dominate today's options volume.
_PAGE_SIZE = 250
_DEFAULT_PAGES = 2  # 500 contracts — enough for a stable top-20
_MAX_RETRIES = 3

TOP_COMMANDS = frozenset({"top", "leaders", "hot", "most-active", "most_active"})


@dataclass
class UnderlyingVolume:
    """Aggregated options activity for one underlying stock/ETF/index."""

    rank: int
    symbol: str
    name: str
    options_volume: int
    open_interest: int
    call_volume: int
    put_volume: int
    active_contracts: int
    spot_price: float | None = None
    change_pct: float | None = None

    @property
    def put_call_ratio(self) -> float | None:
        if self.call_volume <= 0:
            return None
        return round(self.put_volume / self.call_volume, 3)


@dataclass
class LeadersResult:
    """Ranked list of underlyings with the most option volume."""

    leaders: list[UnderlyingVolume] = field(default_factory=list)
    contracts_scanned: int = 0
    unique_underlyings: int = 0
    fetched_at: datetime = field(default_factory=datetime.now)
    source: str = "Yahoo Finance most-active options"


def _http_session():
    """Prefer curl_cffi (browser TLS) — Yahoo rate-limits plain requests hard."""
    try:
        from curl_cffi import requests as crequests

        return crequests.Session(impersonate="chrome"), "curl_cffi"
    except Exception:
        import requests

        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
            }
        )
        return s, "requests"


def _yahoo_crumb(session) -> str | None:
    """Yahoo quote endpoints require a crumb cookie pair."""
    try:
        session.get("https://fc.yahoo.com", timeout=15)
    except Exception:
        pass
    try:
        resp = session.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15
        )
        if getattr(resp, "status_code", 0) == 200:
            crumb = (resp.text or "").strip()
            if crumb and "html" not in crumb.lower() and len(crumb) < 100:
                return crumb
    except Exception:
        return None
    return None


def _get_json(session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=30)
            status = getattr(resp, "status_code", 0)
            if status == 429:
                time.sleep(1.5 * (attempt + 1))
                last_err = OptionChainError("rate limited (HTTP 429)")
                continue
            if status >= 400:
                raise OptionChainError(f"HTTP {status}")
            return resp.json()
        except OptionChainError as exc:
            last_err = exc
            time.sleep(0.8 * (attempt + 1))
        except Exception as exc:
            last_err = exc
            time.sleep(0.8 * (attempt + 1))
    raise OptionChainError(
        "Could not download data from Yahoo Finance.\n"
        f"Details: {last_err}\n"
        "Yahoo sometimes rate-limits — wait a few seconds and try again."
    )


def _fetch_most_active_contracts(
    session,
    *,
    pages: int = _DEFAULT_PAGES,
    page_size: int = _PAGE_SIZE,
) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    for page in range(max(1, pages)):
        start = page * page_size
        params = {
            "formatted": "false",
            "lang": "en-US",
            "region": "US",
            "scrIds": "most_actives_options",
            "count": page_size,
            "start": start,
        }
        payload = _get_json(session, YAHOO_SCREENER, params)
        result = (payload.get("finance") or {}).get("result") or []
        if not result:
            err = (payload.get("finance") or {}).get("error")
            msg = (err or {}).get("description") if err else "empty response"
            if quotes:
                break
            raise OptionChainError(
                f"Yahoo Finance did not return a most-active options list ({msg})."
            )

        batch = result[0].get("quotes") or []
        if not batch:
            break
        quotes.extend(batch)
        total = int(result[0].get("total") or 0)
        if start + page_size >= total:
            break
        # Be polite between pages
        time.sleep(0.35)
    return quotes


def _aggregate_by_underlying(
    contracts: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in contracts:
        symbol = (row.get("underlyingSymbol") or "").strip().upper()
        if not symbol:
            continue
        vol = int(row.get("regularMarketVolume") or 0)
        oi = int(row.get("openInterest") or 0)
        opt_type = str(row.get("optionsType") or "").lower()

        bucket = buckets.setdefault(
            symbol,
            {
                "volume": 0,
                "open_interest": 0,
                "call_volume": 0,
                "put_volume": 0,
                "contracts": 0,
            },
        )
        bucket["volume"] += vol
        bucket["open_interest"] += oi
        bucket["contracts"] += 1
        if opt_type == "call":
            bucket["call_volume"] += vol
        elif opt_type == "put":
            bucket["put_volume"] += vol
    return buckets


def _enrich_quotes(session, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch spot price / name for a batch of underlyings."""
    if not symbols:
        return {}
    out: dict[str, dict[str, Any]] = {}
    crumb = _yahoo_crumb(session)
    chunk_size = 50
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        params: dict[str, Any] = {
            "symbols": ",".join(chunk),
            "lang": "en-US",
            "region": "US",
        }
        if crumb:
            params["crumb"] = crumb
        try:
            payload = _get_json(session, YAHOO_QUOTE, params)
        except OptionChainError:
            # Fallback: yfinance fast_info / info for each symbol (slower)
            out.update(_enrich_via_yfinance(chunk))
            continue
        results = (payload.get("quoteResponse") or {}).get("result") or []
        if not results:
            out.update(_enrich_via_yfinance(chunk))
            continue
        for q in results:
            sym = (q.get("symbol") or "").upper()
            if not sym:
                continue
            out[sym] = {
                "name": q.get("shortName") or q.get("longName") or sym,
                "spot": q.get("regularMarketPrice"),
                "change_pct": q.get("regularMarketChangePercent"),
            }
    return out


def _enrich_via_yfinance(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Best-effort fallback when the batch quote API is unauthorized."""
    out: dict[str, dict[str, Any]] = {}
    try:
        import yfinance as yf
    except Exception:
        return out

    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            info = {}
            try:
                info = t.info or {}
            except Exception:
                info = {}
            name = info.get("shortName") or info.get("longName") or sym
            spot = (
                info.get("regularMarketPrice")
                or info.get("currentPrice")
                or info.get("previousClose")
            )
            change = info.get("regularMarketChangePercent")
            if spot is None:
                try:
                    hist = t.history(period="5d")
                    if hist is not None and not hist.empty:
                        spot = float(hist["Close"].iloc[-1])
                except Exception:
                    pass
            out[sym.upper()] = {
                "name": name,
                "spot": spot,
                "change_pct": change,
            }
        except Exception:
            continue
    return out


def fetch_option_volume_leaders(
    *,
    top_n: int = 20,
    pages: int = _DEFAULT_PAGES,
) -> LeadersResult:
    """
    Return the top ``top_n`` underlyings ranked by options trading volume.

    Method: download Yahoo's most-active option contracts, sum volume by
    underlying symbol, rank descending.
    """
    if top_n < 1:
        raise OptionChainError("--count must be at least 1.")
    if top_n > 100:
        raise OptionChainError("--count cannot exceed 100.")

    with _quiet_yfinance():
        session, _backend = _http_session()
        contracts = _fetch_most_active_contracts(session, pages=pages)
        if not contracts:
            raise OptionChainError(
                "No most-active option contracts were returned. "
                "Try again during US market hours."
            )

        buckets = _aggregate_by_underlying(contracts)
        ranked_syms = sorted(
            buckets.keys(),
            key=lambda s: buckets[s]["volume"],
            reverse=True,
        )[:top_n]

        meta = _enrich_quotes(session, ranked_syms)

        leaders: list[UnderlyingVolume] = []
        for rank, symbol in enumerate(ranked_syms, start=1):
            b = buckets[symbol]
            info = meta.get(symbol, {})
            spot = info.get("spot")
            change = info.get("change_pct")
            try:
                spot_f = float(spot) if spot is not None else None
            except (TypeError, ValueError):
                spot_f = None
            try:
                change_f = float(change) if change is not None else None
            except (TypeError, ValueError):
                change_f = None

            leaders.append(
                UnderlyingVolume(
                    rank=rank,
                    symbol=symbol,
                    name=str(info.get("name") or symbol),
                    options_volume=int(b["volume"]),
                    open_interest=int(b["open_interest"]),
                    call_volume=int(b["call_volume"]),
                    put_volume=int(b["put_volume"]),
                    active_contracts=int(b["contracts"]),
                    spot_price=spot_f,
                    change_pct=change_f,
                )
            )

        return LeadersResult(
            leaders=leaders,
            contracts_scanned=len(contracts),
            unique_underlyings=len(buckets),
        )
