"""ITM vs OTM comparison helpers for long option research."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

import pandas as pd

from optionchain.fetcher import (
    OptionChainData,
    OptionChainError,
    fetch_option_chain,
    list_expiries,
    parse_date,
)
from optionchain.filters import select_expiries

OptionSide = Literal["call", "put"]
StyleBucket = Literal["deep_itm", "itm", "atm", "otm", "far_otm"]


@dataclass
class StrikeCompareRow:
    """One strike evaluated for a long call/put decision."""

    strike: float
    option_type: OptionSide
    last: float
    bid: float
    ask: float
    mid: float
    volume: int
    open_interest: int
    iv: float
    intrinsic: float
    extrinsic: float
    break_even: float
    move_to_be_pct: float
    moneyness_pct: float  # + = ITM for this side, − = OTM
    bucket: StyleBucket
    premium_per_contract: float  # mid or last * 100
    leverage_proxy: float | None  # spot / premium (per share) — rough
    contract_symbol: str = ""

    @property
    def moneyness_label(self) -> str:
        pct = abs(self.moneyness_pct)
        if self.bucket == "atm":
            return f"ATM (~{pct:.1f}%)"
        side = "ITM" if self.moneyness_pct >= 0 else "OTM"
        return f"{side} {pct:.1f}%"


@dataclass
class CompareResult:
    """Structured ITM/OTM comparison for one underlying + expiry + side."""

    symbol: str
    company_name: str
    spot_price: float
    currency: str
    expiry: str
    option_type: OptionSide
    dte: int | None
    rows: list[StrikeCompareRow] = field(default_factory=list)
    target_move_pct: float | None = None
    budget: float | None = None  # dollars for one contract (premium*100)
    if_spot: float | None = None
    fetched_at: datetime = field(default_factory=datetime.now)


def _mid_price(last: float, bid: float, ask: float) -> float:
    if bid > 0 and ask > 0 and ask >= bid:
        return (bid + ask) / 2.0
    if last > 0:
        return last
    if bid > 0:
        return bid
    if ask > 0:
        return ask
    return 0.0


def intrinsic_value(option_type: OptionSide, spot: float, strike: float) -> float:
    if option_type == "call":
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def moneyness_pct(option_type: OptionSide, spot: float, strike: float) -> float:
    """
    Signed moneyness in percent of spot.
    Positive = in the money for that side; negative = out of the money.
    """
    if spot <= 0:
        return 0.0
    if option_type == "call":
        # ITM when spot > strike → positive
        return 100.0 * (spot - strike) / spot
    # put ITM when strike > spot
    return 100.0 * (strike - spot) / spot


def classify_bucket(mny_pct: float) -> StyleBucket:
    """Bucket by signed moneyness (positive ITM)."""
    if mny_pct >= 5.0:
        return "deep_itm"
    if mny_pct >= 1.0:
        return "itm"
    if mny_pct > -1.0:
        return "atm"
    if mny_pct > -5.0:
        return "otm"
    return "far_otm"


def break_even_price(option_type: OptionSide, strike: float, premium: float) -> float:
    if option_type == "call":
        return strike + premium
    return strike - premium


def move_to_break_even_pct(option_type: OptionSide, spot: float, be: float) -> float:
    if spot <= 0:
        return 0.0
    return 100.0 * (be - spot) / spot


def build_compare_row(
    row: pd.Series,
    *,
    spot: float,
    option_type: OptionSide,
) -> StrikeCompareRow | None:
    strike = float(row.get("strike") or 0)
    if strike <= 0 or spot <= 0:
        return None
    last = float(row.get("lastPrice") or 0)
    bid = float(row.get("bid") or 0)
    ask = float(row.get("ask") or 0)
    mid = _mid_price(last, bid, ask)
    if mid <= 0 and last <= 0:
        # unusable quote for comparison
        premium = 0.0
    else:
        premium = mid if mid > 0 else last

    intr = intrinsic_value(option_type, spot, strike)
    # Extrinsic can't be negative in theory; clamp noise
    extr = max(premium - intr, 0.0) if premium > 0 else 0.0
    be = break_even_price(option_type, strike, premium) if premium > 0 else strike
    mny = moneyness_pct(option_type, spot, strike)
    lev = (spot / premium) if premium > 0 else None

    return StrikeCompareRow(
        strike=strike,
        option_type=option_type,
        last=last,
        bid=bid,
        ask=ask,
        mid=mid,
        volume=int(row.get("volume") or 0),
        open_interest=int(row.get("openInterest") or 0),
        iv=float(row.get("impliedVolatility") or 0),
        intrinsic=round(intr, 4),
        extrinsic=round(extr, 4),
        break_even=round(be, 4),
        move_to_be_pct=round(move_to_break_even_pct(option_type, spot, be), 3),
        moneyness_pct=round(mny, 3),
        bucket=classify_bucket(mny),
        premium_per_contract=round(premium * 100.0, 2),
        leverage_proxy=round(lev, 2) if lev is not None else None,
        contract_symbol=str(row.get("contractSymbol") or ""),
    )


def _pick_representative(
    candidates: list[StrikeCompareRow],
    bucket: StyleBucket,
    *,
    spot: float,
) -> StrikeCompareRow | None:
    pool = [r for r in candidates if r.bucket == bucket]
    if not pool:
        return None
    # Prefer liquid contracts, then closest to ideal moneyness for the bucket
    ideals = {
        "deep_itm": 8.0,
        "itm": 3.0,
        "atm": 0.0,
        "otm": -3.0,
        "far_otm": -8.0,
    }
    ideal = ideals[bucket]

    def score(r: StrikeCompareRow) -> tuple:
        # Prefer liquid strikes first (dead deep ITM quotes are common noise)
        liq = r.volume + r.open_interest * 0.35
        dist = abs(r.moneyness_pct - ideal)
        prem_pen = 0 if (r.mid > 0 or r.last > 0) else 1
        # Higher liquidity better → negate; closer to ideal moneyness better
        return (prem_pen, -liq, dist)

    return sorted(pool, key=score)[0]


def select_style_rows(
    all_rows: list[StrikeCompareRow],
    *,
    spot: float,
) -> list[StrikeCompareRow]:
    """Pick one strike per style bucket (deep ITM → far OTM)."""
    order: list[StyleBucket] = ["deep_itm", "itm", "atm", "otm", "far_otm"]
    picked: list[StrikeCompareRow] = []
    seen_strikes: set[float] = set()
    for bucket in order:
        row = _pick_representative(all_rows, bucket, spot=spot)
        if row is None:
            continue
        if row.strike in seen_strikes:
            continue
        picked.append(row)
        seen_strikes.add(row.strike)
    # Fallback: if we got almost nothing, take 5 closest to ATM by |moneyness|
    if len(picked) < 2 and all_rows:
        closest = sorted(all_rows, key=lambda r: abs(r.moneyness_pct))[:5]
        return closest
    return picked


def filter_compare_rows(
    rows: list[StrikeCompareRow],
    *,
    target_move_pct: float | None = None,
    budget: float | None = None,
    option_type: OptionSide,
) -> list[StrikeCompareRow]:
    out = list(rows)
    if budget is not None and budget > 0:
        out = [r for r in out if r.premium_per_contract <= budget + 1e-6]
    if target_move_pct is not None:
        # Keep strikes whose break-even is within the expected move (directional)
        # call: need move_to_be <= target (stock can rise enough)
        # put: need move_to_be >= -target (stock can fall enough)
        t = abs(target_move_pct)
        filtered = []
        for r in out:
            if option_type == "call":
                if r.move_to_be_pct <= t + 0.05:
                    filtered.append(r)
            else:
                if r.move_to_be_pct >= -t - 0.05:
                    filtered.append(r)
        out = filtered
    return out


def expiry_value_if_spot(
    option_type: OptionSide, strike: float, future_spot: float
) -> float:
    """Intrinsic at expiry if underlying finishes at future_spot."""
    return intrinsic_value(option_type, future_spot, strike)


def compare_itm_otm(
    symbol: str,
    *,
    option_type: OptionSide = "call",
    expiry: str | None = None,
    target_move_pct: float | None = None,
    budget: float | None = None,
    if_spot: float | None = None,
    style_picks_only: bool = True,
) -> CompareResult:
    """
    Build an ITM vs OTM comparison table for a long call or long put.

    Parameters
    ----------
    option_type:
        ``call`` or ``put`` (not both — comparison is directional).
    expiry:
        YYYY-MM-DD or nearest listed expiry.
    target_move_pct:
        If set, keep strikes that can break even if the stock moves about this %.
    budget:
        Max premium dollars for **one** contract (premium × 100).
    if_spot:
        Optional scenario spot at expiry for intrinsic-only value.
    style_picks_only:
        If True, show one representative strike per style bucket.
    """
    side = option_type.strip().lower()
    if side not in {"call", "put"}:
        raise OptionChainError("--compare requires call or put (not all).")

    if target_move_pct is not None and target_move_pct <= 0:
        raise OptionChainError("--target-move must be a positive percent (e.g. 5).")
    if budget is not None and budget <= 0:
        raise OptionChainError("--budget must be a positive dollar amount.")
    if if_spot is not None and if_spot <= 0:
        raise OptionChainError("--if-spot must be a positive price.")

    ticker, company, spot, available = list_expiries(symbol)
    selected = select_expiries(available, expiry=expiry, nearest=1)
    data: OptionChainData = fetch_option_chain(ticker, expiries=selected)
    if data.spot_price > 0:
        spot = data.spot_price

    frame = data.calls if side == "call" else data.puts
    if frame is None or frame.empty:
        raise OptionChainError(
            f"No {side} contracts found for {ticker} expiring {selected[0]}."
        )

    all_rows: list[StrikeCompareRow] = []
    for _, raw in frame.iterrows():
        built = build_compare_row(raw, spot=spot, option_type=side)  # type: ignore[arg-type]
        if built is not None:
            all_rows.append(built)

    if not all_rows:
        raise OptionChainError("Could not build comparison rows from the chain.")

    if style_picks_only:
        rows = select_style_rows(all_rows, spot=spot)
    else:
        rows = sorted(all_rows, key=lambda r: r.strike)

    rows = filter_compare_rows(
        rows,
        target_move_pct=target_move_pct,
        budget=budget,
        option_type=side,  # type: ignore[arg-type]
    )
    if not rows:
        raise OptionChainError(
            "No strikes left after filters.\n"
            "Try a larger --budget, a larger --target-move, or drop those flags."
        )

    # DTE
    dte: int | None
    try:
        exp_d = parse_date(selected[0], "expiry")
        dte = (exp_d - date.today()).days
    except OptionChainError:
        dte = None

    return CompareResult(
        symbol=ticker,
        company_name=company,
        spot_price=float(spot),
        currency=data.currency,
        expiry=selected[0],
        option_type=side,  # type: ignore[arg-type]
        dte=dte,
        rows=rows,
        target_move_pct=target_move_pct,
        budget=budget,
        if_spot=if_spot,
    )


def style_guidance(result: CompareResult) -> list[str]:
    """Plain-English research notes — not trade recommendations."""
    lines: list[str] = []
    side = result.option_type.upper()
    lines.append(
        f"Comparing long {side}s on {result.symbol} @ {result.spot_price:,.2f} "
        f"for expiry {result.expiry}"
        + (f" ({result.dte}d left)." if result.dte is not None else ".")
    )
    lines.append(
        "ITM = already has intrinsic value (costs more, usually tracks the stock better)."
    )
    lines.append(
        "OTM = cheaper premium, more leverage, needs a bigger move before expiry or it can expire worthless."
    )

    by_bucket = {r.bucket: r for r in result.rows}
    if "itm" in by_bucket or "deep_itm" in by_bucket:
        r = by_bucket.get("itm") or by_bucket.get("deep_itm")
        assert r is not None
        lines.append(
            f"More conservative style example: strike {r.strike:g} ({r.moneyness_label}) — "
            f"premium ~${r.premium_per_contract:,.0f}/contract, "
            f"break-even {r.break_even:,.2f} ({r.move_to_be_pct:+.1f}% move)."
        )
    if "otm" in by_bucket or "far_otm" in by_bucket:
        r = by_bucket.get("otm") or by_bucket.get("far_otm")
        assert r is not None
        lines.append(
            f"More aggressive style example: strike {r.strike:g} ({r.moneyness_label}) — "
            f"premium ~${r.premium_per_contract:,.0f}/contract, "
            f"needs about {abs(r.move_to_be_pct):.1f}% favorable move to break even."
        )
    if "atm" in by_bucket:
        r = by_bucket["atm"]
        lines.append(
            f"ATM balance point: strike {r.strike:g} — often the liquidity center "
            f"(vol {r.volume:,}, OI {r.open_interest:,})."
        )

    if result.target_move_pct is not None:
        lines.append(
            f"Filter active: only strikes that can break even on a ~{result.target_move_pct:g}% move."
        )
    if result.budget is not None:
        lines.append(
            f"Filter active: premium ≤ ${result.budget:,.0f} per contract."
        )
    if result.if_spot is not None:
        lines.append(
            f"Scenario: if spot finishes at {result.if_spot:,.2f} at expiry, "
            "see 'Worth@' column (intrinsic only — ignores early exit)."
        )

    lines.append(
        "Research aid only — not a buy/sell recommendation. Check liquidity (Vol/OI) and your risk limit."
    )
    return lines
