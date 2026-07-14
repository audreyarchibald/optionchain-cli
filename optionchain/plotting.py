"""Charts for multi-day option call/put price paths (terminal + optional PNG)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from optionchain.fetcher import OptionChainError
from optionchain.history import ChainHistoryResult, ContractHistory

# One color each — type is told by color; strike is labeled on the line.
CALL_COLOR = "green"
PUT_COLOR = "red"
CALL_COLOR_HEX = "#2ca02c"
PUT_COLOR_HEX = "#d62728"
SPOT_COLOR_HEX = "#1f77b4"


def _strike_label(c: ContractHistory) -> str:
    """Strike only (color already says call vs put)."""
    return f"{c.strike:g}"


def _series_for_contract(
    c: ContractHistory, trade_dates: list[date]
) -> tuple[list[date], list[float]]:
    xs: list[date] = []
    ys: list[float] = []
    for d in trade_dates:
        px = c.close_on(d)
        if px is not None:
            xs.append(d)
            ys.append(px)
    return xs, ys


def _split_calls_puts(
    result: ChainHistoryResult,
) -> tuple[list[ContractHistory], list[ContractHistory]]:
    if not result.contracts:
        raise OptionChainError("No contracts to plot.")
    if not result.trade_dates:
        raise OptionChainError("No trading dates to plot.")
    calls = [c for c in result.contracts if c.option_type == "call"]
    puts = [c for c in result.contracts if c.option_type == "put"]
    if not calls and not puts:
        raise OptionChainError("No call/put series available to plot.")
    return calls, puts


def _default_save_path(result: ChainHistoryResult) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_exp = result.expiry.replace("-", "")
    return Path(f"optionchain_{result.symbol}_{safe_exp}_history_{stamp}.png")


def print_terminal_plot(
    result: ChainHistoryResult,
    *,
    title: str | None = None,
    width: int | None = None,
    height: int = 22,
) -> None:
    """
    Draw call (green) / put (red) price paths **inside the terminal**.

    One green for every call, one red for every put; strike is labeled on each line.
    """
    try:
        import plotext as plt
    except ImportError as exc:
        raise OptionChainError(
            "Terminal plotting needs plotext.\n"
            "  Install with:  uv add plotext"
        ) from exc

    calls, puts = _split_calls_puts(result)
    dates = result.trade_dates
    x_idx = list(range(len(dates)))
    x_labels = [d.strftime("%m/%d") for d in dates]

    plt.clear_figure()
    if width is not None:
        plt.plotsize(width, height)
    else:
        try:
            plt.plotsize(min(max(plt.terminal_width() - 2, 60), 120), height)
        except Exception:
            plt.plotsize(90, height)

    chart_title = title or (
        f"{result.symbol}  green=calls  red=puts  |  exp {result.expiry}  |  "
        f"{len(dates)} sessions"
    )
    plt.title(chart_title)
    plt.xlabel("session")
    plt.ylabel("option close $")

    def _draw(group: list[ContractHistory], color: str) -> None:
        for c in sorted(group, key=lambda x: x.strike):
            xs, ys = _series_for_contract(c, dates)
            if len(xs) < 1:
                continue
            xi = [dates.index(d) for d in xs]
            # One shared color per type; strike shown as the series label
            plt.plot(
                xi,
                ys,
                label=_strike_label(c),
                color=color,
                marker="braille",
            )

    _draw(calls, CALL_COLOR)
    _draw(puts, PUT_COLOR)

    plt.xticks(x_idx, x_labels)
    plt.theme("clear")
    plt.grid(True, True)
    plt.show()

    spot_bits = []
    for d in dates:
        px = result.spot_by_date.get(d)
        if px is None:
            spot_bits.append(f"{d.strftime('%m/%d')}:—")
        else:
            spot_bits.append(f"{d.strftime('%m/%d')}:{px:,.2f}")
    print(f"  Underlying {result.symbol}:  " + "  →  ".join(spot_bits))
    print("  Legend: green = CALLS  ·  red = PUTS  ·  number = strike price")
    print("  Tip: save a high-res PNG with  --save  or  --save ./my_chart.png")


def _padded_ylim(
    values: list[float],
    *,
    floor_at_zero: bool = False,
    pad_frac: float = 0.18,
    min_pad_abs: float = 0.05,
    min_pad_pct_of_level: float = 0.008,
) -> tuple[float, float] | None:
    """
    Zoom y-limits to the data range so small moves are visible.

    Without this, a $55 stock plotted from $0 looks almost flat.
    """
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    lo = min(nums)
    hi = max(nums)
    span = hi - lo
    # Flat or nearly flat series: still open a window around the level
    if span < 1e-12:
        base = abs(hi) if abs(hi) > 1e-12 else 1.0
        pad = max(base * 0.02, min_pad_abs)
    else:
        pad = max(span * pad_frac, abs(hi) * min_pad_pct_of_level, min_pad_abs)
    y0, y1 = lo - pad, hi + pad
    if floor_at_zero:
        # Options can't go below 0; keep a little headroom above zero only if needed
        y0 = max(0.0, y0)
        if y1 <= y0:
            y1 = y0 + min_pad_abs
    return y0, y1


def build_chain_history_figure(
    result: ChainHistoryResult,
    *,
    title: str | None = None,
    figsize: tuple[float, float] = (10, 6.5),
):
    """
    Build a matplotlib Figure for call/put history (for GUI embed or save).

    Y-axes auto-zoom to the data range (with padding) so price moves
    stay visible — especially the underlying panel, which must not
    always start at $0.

    Caller owns the figure (close when done if not embedding).
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.lines import Line2D
    except ImportError as exc:
        raise OptionChainError(
            "Plotting needs matplotlib.\n"
            "  Install with:  uv add matplotlib"
        ) from exc

    calls, puts = _split_calls_puts(result)

    fig, axes = plt.subplots(
        2,
        1,
        figsize=figsize,
        sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1.6]},
    )
    ax_opt, ax_spot = axes

    option_ys: list[float] = []

    def _plot_group(group: list[ContractHistory], color: str) -> None:
        for c in sorted(group, key=lambda x: x.strike):
            xs, ys = _series_for_contract(c, result.trade_dates)
            if len(xs) < 1:
                continue
            option_ys.extend(ys)
            ax_opt.plot(
                xs,
                ys,
                marker="o",
                markersize=4.5,
                linewidth=1.8,
                color=color,
            )
            ax_opt.annotate(
                _strike_label(c),
                xy=(xs[-1], ys[-1]),
                xytext=(6, 0),
                textcoords="offset points",
                color=color,
                fontsize=8,
                fontweight="bold",
                va="center",
                ha="left",
                clip_on=False,
            )

    _plot_group(calls, CALL_COLOR_HEX)
    _plot_group(puts, PUT_COLOR_HEX)

    chart_title = title or (
        f"{result.symbol} options — calls (green) vs puts (red)\n"
        f"Expiry {result.expiry}  ·  last {len(result.trade_dates)} sessions"
    )
    ax_opt.set_title(chart_title, fontsize=12, pad=10)
    ax_opt.set_ylabel("Option close ($)")
    ax_opt.grid(True, alpha=0.28, linestyle="--")
    ax_opt.legend(
        handles=[
            Line2D([0], [0], color=CALL_COLOR_HEX, lw=2, label="Calls"),
            Line2D([0], [0], color=PUT_COLOR_HEX, lw=2, label="Puts"),
        ],
        loc="best",
        fontsize=9,
        framealpha=0.92,
    )
    opt_lim = _padded_ylim(option_ys, floor_at_zero=True, pad_frac=0.2)
    if opt_lim is not None:
        ax_opt.set_ylim(*opt_lim)

    spot_x: list[date] = []
    spot_y: list[float] = []
    for d in result.trade_dates:
        px = result.spot_by_date.get(d)
        if px is not None:
            spot_x.append(d)
            spot_y.append(px)
    if spot_x:
        ax_spot.plot(
            spot_x,
            spot_y,
            marker="s",
            markersize=5,
            linewidth=2.2,
            color=SPOT_COLOR_HEX,
            label=f"{result.symbol} spot",
        )
        spot_lim = _padded_ylim(
            spot_y,
            floor_at_zero=False,
            pad_frac=0.25,
            min_pad_abs=0.15,
            min_pad_pct_of_level=0.01,
        )
        if spot_lim is not None:
            y0, y1 = spot_lim
            ax_spot.set_ylim(y0, y1)
            # Shade from the zoomed floor up to the line (not from $0)
            ax_spot.fill_between(spot_x, spot_y, y0, alpha=0.18, color=SPOT_COLOR_HEX)

    ax_spot.set_ylabel("Stock price ($)")
    ax_spot.set_xlabel("Date")
    ax_spot.grid(True, alpha=0.28, linestyle="--")
    ax_spot.legend(loc="best", fontsize=8, framealpha=0.92)
    ax_spot.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax_spot.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    fig.text(
        0.01,
        0.01,
        "Green = calls · Red = puts · number on line = strike  |  "
        "Y-axes auto-zoom to data  |  Yahoo Finance",
        fontsize=8,
        color="#666666",
    )
    return fig


def save_chain_history_plot(
    result: ChainHistoryResult,
    *,
    path: str | Path | None = None,
    title: str | None = None,
) -> Path:
    """
    Save a high-resolution call/put chart as PNG (matplotlib).

    One green for calls, one red for puts; each line labeled with its strike.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise OptionChainError(
            "Saving plots needs matplotlib.\n"
            "  Install with:  uv add matplotlib"
        ) from exc

    fig = build_chain_history_figure(result, title=title)
    out = Path(path) if path is not None else _default_save_path(result)
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def plot_chain_history(
    result: ChainHistoryResult,
    *,
    path: str | Path | None = None,
    show: bool = True,
    title: str | None = None,
    terminal: bool = True,
) -> Path | None:
    """
    Plot call/put history.

    - ``terminal=True`` (default): draw in the terminal.
    - ``path`` set: also save a PNG and return that path.
    """
    if terminal:
        print_terminal_plot(result, title=title)
    if path is not None or not terminal:
        return save_chain_history_plot(result, path=path, title=title)
    return None
