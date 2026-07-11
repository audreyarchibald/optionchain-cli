"""Option-chain metrics such as put/call ratio."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class PutCallRatio:
    """Put/call ratio computed from volume and open interest."""

    call_volume: int
    put_volume: int
    call_open_interest: int
    put_open_interest: int
    volume_ratio: float | None
    oi_ratio: float | None

    @property
    def volume_interpretation(self) -> str:
        return _interpret(self.volume_ratio, basis="volume")

    @property
    def oi_interpretation(self) -> str:
        return _interpret(self.oi_ratio, basis="open interest")


# Minimum activity before we trust a ratio (avoids 0.000 noise when data is sparse)
_MIN_VOLUME = 10
_MIN_OI = 50


def _safe_ratio(
    numerator: int, denominator: int, *, min_denom: int = 1
) -> float | None:
    if denominator < min_denom:
        return None
    return round(numerator / denominator, 3)


def _interpret(ratio: float | None, basis: str) -> str:
    if ratio is None:
        return (
            f"Not enough call {basis} yet to compute a reliable put/call ratio "
            "(common right after the open, after hours, or on thin contracts)."
        )
    if ratio < 0.7:
        return (
            f"PCR {ratio:.2f} (by {basis}): more call activity than put activity. "
            "Often read as relatively bullish / less hedging demand."
        )
    if ratio <= 1.0:
        return (
            f"PCR {ratio:.2f} (by {basis}): fairly balanced put vs call activity."
        )
    if ratio <= 1.3:
        return (
            f"PCR {ratio:.2f} (by {basis}): slightly more puts than calls. "
            "Mildly cautious / hedging-leaning."
        )
    return (
        f"PCR {ratio:.2f} (by {basis}): clearly more puts than calls. "
        "Often read as relatively bearish or heavy hedging "
        "(context matters — this is not a trading signal)."
    )


def compute_put_call_ratio(calls: pd.DataFrame, puts: pd.DataFrame) -> PutCallRatio:
    """Compute put/call ratios from volume and open interest columns."""
    call_vol = int(calls["volume"].fillna(0).sum()) if not calls.empty else 0
    put_vol = int(puts["volume"].fillna(0).sum()) if not puts.empty else 0
    call_oi = (
        int(calls["openInterest"].fillna(0).sum()) if not calls.empty else 0
    )
    put_oi = int(puts["openInterest"].fillna(0).sum()) if not puts.empty else 0

    return PutCallRatio(
        call_volume=call_vol,
        put_volume=put_vol,
        call_open_interest=call_oi,
        put_open_interest=put_oi,
        volume_ratio=_safe_ratio(put_vol, call_vol, min_denom=_MIN_VOLUME),
        oi_ratio=_safe_ratio(put_oi, call_oi, min_denom=_MIN_OI),
    )



def summarize_chain(df: pd.DataFrame, spot_price: float) -> dict[str, object]:
    """Lightweight summary stats for the filtered chain."""
    if df is None or df.empty:
        return {
            "rows": 0,
            "expiries": 0,
            "strikes": 0,
            "itm_count": 0,
            "otm_count": 0,
            "total_volume": 0,
            "total_oi": 0,
        }

    itm = int(df["inTheMoney"].sum()) if "inTheMoney" in df.columns else 0
    return {
        "rows": int(len(df)),
        "expiries": int(df["expiry"].nunique()) if "expiry" in df.columns else 0,
        "strikes": int(df["strike"].nunique()) if "strike" in df.columns else 0,
        "itm_count": itm,
        "otm_count": int(len(df) - itm),
        "total_volume": int(df["volume"].fillna(0).sum()) if "volume" in df.columns else 0,
        "total_oi": (
            int(df["openInterest"].fillna(0).sum())
            if "openInterest" in df.columns
            else 0
        ),
        "spot_price": float(spot_price),
    }
