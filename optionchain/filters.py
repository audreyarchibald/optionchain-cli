"""Filter option chains by type, expiry, strike, and moneyness helpers."""

from __future__ import annotations

from datetime import date

import pandas as pd

from optionchain.fetcher import OptionChainError, parse_date


def select_expiries(
    available: list[str],
    *,
    expiry: str | None = None,
    expiry_from: str | None = None,
    expiry_to: str | None = None,
    nearest: int | None = None,
) -> list[str]:
    """
    Choose which expiry dates to fetch/display.

    Priority:
    1. Exact --expiry
    2. Date range --from / --to
    3. --nearest N (soonest N expiries)
    4. Default: nearest 1 expiry only (keeps output readable for beginners)
    """
    if not available:
        raise OptionChainError("No expiry dates are available for this symbol.")

    # Exact single expiry
    if expiry:
        exp = parse_date(expiry, "expiry").isoformat()
        if exp not in available:
            sample = ", ".join(available[:6])
            more = f" (+{len(available) - 6} more)" if len(available) > 6 else ""
            raise OptionChainError(
                f"Expiry {exp} is not listed for this stock.\n"
                f"Closest available dates include: {sample}{more}\n"
                "Tip: use --list-expiries to see every available date."
            )
        return [exp]

    # Date range filter
    if expiry_from or expiry_to:
        start = parse_date(expiry_from, "start date") if expiry_from else date.min
        end = parse_date(expiry_to, "end date") if expiry_to else date.max
        if start > end:
            raise OptionChainError(
                f"Start date ({start}) is after end date ({end}). "
                "Swap --from and --to, or pick a wider range."
            )
        selected = [
            e
            for e in available
            if start <= parse_date(e, "expiry") <= end
        ]
        if not selected:
            sample = ", ".join(available[:6])
            raise OptionChainError(
                f"No expiries fall between {start} and {end}.\n"
                f"Available dates include: {sample}"
                + (f" (+{len(available) - 6} more)" if len(available) > 6 else "")
            )
        return selected

    # Nearest N expiries (default N=1 when nearest is None)
    n = 1 if nearest is None else nearest
    if n < 1:
        raise OptionChainError("--nearest must be at least 1.")
    return available[:n]


def filter_by_type(calls: pd.DataFrame, puts: pd.DataFrame, option_type: str) -> pd.DataFrame:
    """Return a combined chain filtered to call, put, or both."""
    kind = (option_type or "all").strip().lower()
    if kind in {"call", "calls", "c"}:
        return calls.copy()
    if kind in {"put", "puts", "p"}:
        return puts.copy()
    if kind in {"all", "both", "a"}:
        if calls.empty and puts.empty:
            return calls.copy()
        return pd.concat([calls, puts], ignore_index=True)
    raise OptionChainError(
        f"Unknown option type '{option_type}'. Use: call, put, or all."
    )


def filter_by_strike(
    df: pd.DataFrame,
    *,
    strike_min: float | None = None,
    strike_max: float | None = None,
    spot_price: float | None = None,
    near: int | None = None,
) -> pd.DataFrame:
    """Filter rows by strike range and/or N strikes nearest to the spot price."""
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()

    out = df.copy()

    if strike_min is not None:
        out = out[out["strike"] >= float(strike_min)]
    if strike_max is not None:
        out = out[out["strike"] <= float(strike_max)]

    if strike_min is not None and strike_max is not None and strike_min > strike_max:
        raise OptionChainError(
            f"Minimum strike ({strike_min}) is greater than maximum strike ({strike_max})."
        )

    if near is not None:
        if near < 1:
            raise OptionChainError("--near must be at least 1.")
        ref = float(spot_price or 0.0)
        if ref <= 0 and not out.empty:
            ref = float(out["strike"].median())

        # For multi-expiry frames, apply near-filter per expiry + type group
        group_cols = [c for c in ("expiry", "type") if c in out.columns]
        if group_cols:
            pieces = []
            for _, group in out.groupby(group_cols, sort=False):
                g = group.copy()
                g["_dist"] = (g["strike"] - ref).abs()
                pieces.append(
                    g.nsmallest(near, "_dist").drop(columns="_dist")
                )
            out = (
                pd.concat(pieces, ignore_index=True)
                if pieces
                else out.iloc[0:0]
            )
        else:
            out = out.assign(_dist=(out["strike"] - ref).abs()).nsmallest(
                near, "_dist"
            ).drop(columns="_dist")

    return out.sort_values(
        [c for c in ("expiry", "type", "strike") if c in out.columns]
    ).reset_index(drop=True)


def apply_filters(
    calls: pd.DataFrame,
    puts: pd.DataFrame,
    *,
    option_type: str = "all",
    strike_min: float | None = None,
    strike_max: float | None = None,
    spot_price: float | None = None,
    near: int | None = None,
) -> pd.DataFrame:
    """Combine type + strike filters into one DataFrame for display."""
    combined = filter_by_type(calls, puts, option_type)
    return filter_by_strike(
        combined,
        strike_min=strike_min,
        strike_max=strike_max,
        spot_price=spot_price,
        near=near,
    )
