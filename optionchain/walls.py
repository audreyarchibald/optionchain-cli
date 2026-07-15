"""
Call wall / put wall analysis for gamma-style positioning research.

Definitions (research convention, not a trading signal):
  • Call wall — strike with the heaviest **call** open interest at or above spot
    (fallback: max call OI anywhere). Often discussed as upside resistance /
    dealer hedge supply.
  • Put wall  — strike with the heaviest **put** open interest at or below spot
    (fallback: max put OI anywhere). Often discussed as downside support /
    dealer hedge demand.
  • Max pain  — strike where total option value (calls+puts) is minimized for
    holders at expiry (classic OI-based estimate).

"Gamma evaluation" here means plain-English positioning notes between the walls,
not a full GEX model (that needs dealer positioning assumptions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from optionchain.fetcher import (
    OptionChainData,
    OptionChainError,
    fetch_option_chain,
    list_expiries,
    parse_date,
)
from optionchain.filters import select_expiries


@dataclass
class WallLevel:
    """One OI/volume concentration level."""

    strike: float
    open_interest: int
    volume: int
    side: str  # "call" | "put"
    distance_pct: float  # (strike - spot) / spot * 100
    share_of_side_oi: float  # fraction of total call or put OI
    rank: int = 1

    @property
    def label(self) -> str:
        return f"{self.strike:g}"


@dataclass
class WallAnalysis:
    """Full wall + max-pain + gamma-style evaluation for one expiry."""

    symbol: str
    company_name: str
    spot_price: float
    currency: str
    expiry: str
    dte: int | None
    call_wall: WallLevel | None
    put_wall: WallLevel | None
    top_call_walls: list[WallLevel] = field(default_factory=list)
    top_put_walls: list[WallLevel] = field(default_factory=list)
    max_pain: float | None = None
    total_call_oi: int = 0
    total_put_oi: int = 0
    total_call_vol: int = 0
    total_put_vol: int = 0
    pin_range_low: float | None = None  # min(put_wall, call_wall)
    pin_range_high: float | None = None
    spot_in_pin_range: bool | None = None
    evaluation: list[str] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=datetime.now)


def _oi_vol_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["strike", "openInterest", "volume"])
    out = df.copy()
    out["strike"] = pd.to_numeric(out.get("strike"), errors="coerce")
    out["openInterest"] = (
        pd.to_numeric(out.get("openInterest"), errors="coerce").fillna(0).astype(int)
    )
    out["volume"] = pd.to_numeric(out.get("volume"), errors="coerce").fillna(0).astype(int)
    out = out.dropna(subset=["strike"])
    # Aggregate duplicate strikes
    out = (
        out.groupby("strike", as_index=False)[["openInterest", "volume"]]
        .sum()
        .sort_values("strike")
    )
    return out


def _wall_from_row(
    strike: float,
    oi: int,
    vol: int,
    *,
    side: str,
    spot: float,
    total_side_oi: int,
    rank: int,
) -> WallLevel:
    dist = 0.0 if spot <= 0 else 100.0 * (strike - spot) / spot
    share = (oi / total_side_oi) if total_side_oi > 0 else 0.0
    return WallLevel(
        strike=float(strike),
        open_interest=int(oi),
        volume=int(vol),
        side=side,
        distance_pct=round(dist, 3),
        share_of_side_oi=round(share, 4),
        rank=rank,
    )


def find_walls(
    calls: pd.DataFrame,
    puts: pd.DataFrame,
    spot: float,
    *,
    top_n: int = 5,
) -> tuple[WallLevel | None, WallLevel | None, list[WallLevel], list[WallLevel], int, int]:
    """
    Identify primary call/put walls and top-N OI strikes per side.
    """
    cdf = _oi_vol_frame(calls)
    pdf = _oi_vol_frame(puts)
    total_call_oi = int(cdf["openInterest"].sum()) if not cdf.empty else 0
    total_put_oi = int(pdf["openInterest"].sum()) if not pdf.empty else 0

    top_calls: list[WallLevel] = []
    top_puts: list[WallLevel] = []

    if not cdf.empty and total_call_oi > 0:
        ranked = cdf.sort_values(
            ["openInterest", "volume"], ascending=False
        ).head(top_n)
        for i, (_, r) in enumerate(ranked.iterrows(), start=1):
            top_calls.append(
                _wall_from_row(
                    r["strike"],
                    int(r["openInterest"]),
                    int(r["volume"]),
                    side="call",
                    spot=spot,
                    total_side_oi=total_call_oi,
                    rank=i,
                )
            )

    if not pdf.empty and total_put_oi > 0:
        ranked = pdf.sort_values(
            ["openInterest", "volume"], ascending=False
        ).head(top_n)
        for i, (_, r) in enumerate(ranked.iterrows(), start=1):
            top_puts.append(
                _wall_from_row(
                    r["strike"],
                    int(r["openInterest"]),
                    int(r["volume"]),
                    side="put",
                    spot=spot,
                    total_side_oi=total_put_oi,
                    rank=i,
                )
            )

    # Primary call wall: max OI among strikes >= spot; else global max call OI
    call_wall: WallLevel | None = None
    if not cdf.empty and total_call_oi > 0:
        above = cdf[cdf["strike"] >= spot - 1e-9]
        pool = above if not above.empty else cdf
        r = pool.sort_values(["openInterest", "volume"], ascending=False).iloc[0]
        call_wall = _wall_from_row(
            r["strike"],
            int(r["openInterest"]),
            int(r["volume"]),
            side="call",
            spot=spot,
            total_side_oi=total_call_oi,
            rank=1,
        )

    put_wall: WallLevel | None = None
    if not pdf.empty and total_put_oi > 0:
        below = pdf[pdf["strike"] <= spot + 1e-9]
        pool = below if not below.empty else pdf
        r = pool.sort_values(["openInterest", "volume"], ascending=False).iloc[0]
        put_wall = _wall_from_row(
            r["strike"],
            int(r["openInterest"]),
            int(r["volume"]),
            side="put",
            spot=spot,
            total_side_oi=total_put_oi,
            rank=1,
        )

    return call_wall, put_wall, top_calls, top_puts, total_call_oi, total_put_oi


def compute_max_pain(calls: pd.DataFrame, puts: pd.DataFrame) -> float | None:
    """
    Classic max-pain: strike minimizing total intrinsic value of all open contracts
    (calls + puts) if price settles there.
    """
    cdf = _oi_vol_frame(calls)
    pdf = _oi_vol_frame(puts)
    if cdf.empty and pdf.empty:
        return None

    strikes = sorted(
        set(cdf["strike"].tolist() if not cdf.empty else [])
        | set(pdf["strike"].tolist() if not pdf.empty else [])
    )
    if not strikes:
        return None

    call_map = (
        dict(zip(cdf["strike"], cdf["openInterest"])) if not cdf.empty else {}
    )
    put_map = dict(zip(pdf["strike"], pdf["openInterest"])) if not pdf.empty else {}

    best_strike = strikes[0]
    best_pain = float("inf")
    for s in strikes:
        pain = 0.0
        for k, oi in call_map.items():
            if s > k:
                pain += (s - k) * oi
        for k, oi in put_map.items():
            if k > s:
                pain += (k - s) * oi
        if pain < best_pain:
            best_pain = pain
            best_strike = s
    return float(best_strike)


def evaluate_walls(
    *,
    spot: float,
    call_wall: WallLevel | None,
    put_wall: WallLevel | None,
    max_pain: float | None,
    total_call_oi: int,
    total_put_oi: int,
    dte: int | None,
) -> list[str]:
    """Plain-English gamma/positioning notes (research, not advice)."""
    lines: list[str] = []

    if call_wall is None and put_wall is None:
        lines.append(
            "Not enough open interest to identify call/put walls "
            "(common after hours or on thin chains)."
        )
        return lines

    if put_wall is not None:
        lines.append(
            f"Put wall at {put_wall.strike:g} "
            f"({put_wall.distance_pct:+.1f}% from spot) — "
            f"OI {put_wall.open_interest:,} "
            f"({100 * put_wall.share_of_side_oi:.1f}% of put OI). "
            "Heavy put OI below spot is often read as a downside magnet / support zone "
            "in dealer-gamma narratives."
        )
    if call_wall is not None:
        lines.append(
            f"Call wall at {call_wall.strike:g} "
            f"({call_wall.distance_pct:+.1f}% from spot) — "
            f"OI {call_wall.open_interest:,} "
            f"({100 * call_wall.share_of_side_oi:.1f}% of call OI). "
            "Heavy call OI above spot is often read as upside resistance / supply."
        )

    if put_wall and call_wall:
        lo = min(put_wall.strike, call_wall.strike)
        hi = max(put_wall.strike, call_wall.strike)
        width = hi - lo
        width_pct = 100.0 * width / spot if spot > 0 else 0.0
        inside = lo <= spot <= hi
        lines.append(
            f"Pin / gamma range ≈ {lo:g} – {hi:g} "
            f"(width {width:.2f} / {width_pct:.1f}% of spot). "
            + (
                "Spot is **inside** the walls — short-gamma pin talk often focuses here."
                if inside
                else "Spot is **outside** the walls — watch for a re-entry or a wall break."
            )
        )

    if max_pain is not None and spot > 0:
        mp_dist = 100.0 * (max_pain - spot) / spot
        lines.append(
            f"Max pain ≈ {max_pain:g} ({mp_dist:+.1f}% from spot). "
            "Classic story: price gravitates toward max pain into expiry — "
            "weak as a signal alone, useful as context next to walls."
        )

    if total_call_oi + total_put_oi > 0:
        pcr_oi = total_put_oi / total_call_oi if total_call_oi else None
        if pcr_oi is not None:
            lines.append(
                f"Expiry OI put/call = {pcr_oi:.2f} "
                f"(puts {total_put_oi:,} / calls {total_call_oi:,})."
            )

    if dte is not None:
        if dte <= 2:
            lines.append(
                f"DTE = {dte}: walls and max pain matter more into the final sessions "
                "(pin risk rises; OI can still shift)."
            )
        elif dte <= 10:
            lines.append(
                f"DTE = {dte}: intermediate horizon — walls are reference levels, "
                "not hard barriers."
            )
        else:
            lines.append(
                f"DTE = {dte}: longer-dated OI walls are softer; "
                "near-term walls (if any) usually dominate price talk."
            )

    lines.append(
        "Research framing only — not a prediction. Dealer gamma depends on who is long/short; "
        "Yahoo OI is delayed/incomplete. Combine with volume, spot structure, and liquidity."
    )
    return lines


def analyze_walls(
    symbol: str,
    *,
    expiry: str | None = None,
    top_n: int = 5,
) -> WallAnalysis:
    """Fetch chain and compute call wall, put wall, max pain, evaluation."""
    if top_n < 1:
        raise OptionChainError("--top-walls must be at least 1.")
    if top_n > 20:
        raise OptionChainError("--top-walls cannot exceed 20.")

    ticker, company, spot, available = list_expiries(symbol)
    if expiry:
        selected = select_expiries(available, expiry=expiry)
    else:
        selected = select_expiries(available, nearest=1)

    data: OptionChainData = fetch_option_chain(ticker, expiries=selected)
    if data.spot_price > 0:
        spot = data.spot_price

    exp = selected[0]
    dte: int | None
    try:
        dte = (parse_date(exp, "expiry") - date.today()).days
        # Same-day / timezone edge: never report negative DTE
        if dte is not None and dte < 0:
            dte = 0
    except OptionChainError:
        dte = None

    call_wall, put_wall, top_calls, top_puts, tcoi, tpoi = find_walls(
        data.calls, data.puts, float(spot), top_n=top_n
    )
    max_pain = compute_max_pain(data.calls, data.puts)

    pin_lo = pin_hi = None
    inside = None
    if call_wall and put_wall:
        pin_lo = min(call_wall.strike, put_wall.strike)
        pin_hi = max(call_wall.strike, put_wall.strike)
        inside = pin_lo <= spot <= pin_hi

    call_vol = (
        int(pd.to_numeric(data.calls.get("volume"), errors="coerce").fillna(0).sum())
        if data.calls is not None and not data.calls.empty
        else 0
    )
    put_vol = (
        int(pd.to_numeric(data.puts.get("volume"), errors="coerce").fillna(0).sum())
        if data.puts is not None and not data.puts.empty
        else 0
    )

    notes = evaluate_walls(
        spot=float(spot),
        call_wall=call_wall,
        put_wall=put_wall,
        max_pain=max_pain,
        total_call_oi=tcoi,
        total_put_oi=tpoi,
        dte=dte,
    )

    return WallAnalysis(
        symbol=ticker,
        company_name=company,
        spot_price=float(spot),
        currency=data.currency,
        expiry=exp,
        dte=dte,
        call_wall=call_wall,
        put_wall=put_wall,
        top_call_walls=top_calls,
        top_put_walls=top_puts,
        max_pain=max_pain,
        total_call_oi=tcoi,
        total_put_oi=tpoi,
        total_call_vol=call_vol,
        total_put_vol=put_vol,
        pin_range_low=pin_lo,
        pin_range_high=pin_hi,
        spot_in_pin_range=inside,
        evaluation=notes,
    )


def walls_summary_dict(analysis: WallAnalysis) -> dict[str, Any]:
    """Compact dict for GUI tables / JSON-ish display."""
    return {
        "symbol": analysis.symbol,
        "spot": analysis.spot_price,
        "expiry": analysis.expiry,
        "call_wall": analysis.call_wall.strike if analysis.call_wall else None,
        "put_wall": analysis.put_wall.strike if analysis.put_wall else None,
        "max_pain": analysis.max_pain,
        "pin_low": analysis.pin_range_low,
        "pin_high": analysis.pin_range_high,
        "spot_in_range": analysis.spot_in_pin_range,
    }
