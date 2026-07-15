"""Pretty terminal output for option chains (beginner-friendly)."""

from __future__ import annotations

import shutil
import sys
from typing import Iterable

import pandas as pd
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from optionchain.compare import CompareResult, expiry_value_if_spot, style_guidance
from optionchain.fetcher import OptionChainData
from optionchain.history import ChainHistoryResult
from optionchain.leaders import LeadersResult
from optionchain.metrics import PutCallRatio
from optionchain.walls import WallAnalysis, WallLevel


def _build_console() -> Console:
    # When stdout is piped (tests/CI), Rich defaults to a narrow width and
    # crushes multi-column tables. Prefer a readable fallback width.
    if sys.stdout.isatty():
        return Console()
    width = max(shutil.get_terminal_size(fallback=(120, 40)).columns, 120)
    return Console(width=width)


console = _build_console()


COLUMN_HELP = {
    "Strike": "The price where the option can be exercised.",
    "Last": "Most recent trade price of this option contract.",
    "Bid": "Highest price buyers are currently offering.",
    "Ask": "Lowest price sellers are currently asking.",
    "Vol": "Contracts traded today (higher = more activity).",
    "OI": "Open Interest — contracts still open / not closed.",
    "IV": "Implied Volatility — market's expected move (higher often = pricier options).",
    "ITM": "In The Money — already has intrinsic value if exercised now.",
}


def print_error(message: str) -> None:
    console.print(Panel(message, title="[bold red]Error[/]", border_style="red"))


def print_tip(message: str) -> None:
    console.print(f"[dim]Tip: {message}[/dim]")


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def pcr_sentiment_style(ratio: float | None) -> str:
    """
    Map put/call ratio to a green→yellow→red sentiment color.

    Lower PCR = more call activity = more optimistic → green.
    Higher PCR = more put activity = more cautious → red.
    """
    if ratio is None:
        return "dim"

    # Anchor points (PCR → RGB). Smooth blend between neighbors.
    # 0.40  deep green (very optimistic)
    # 0.70  green
    # 1.00  amber (balanced)
    # 1.30  orange
    # 2.00+ deep red (very cautious)
    stops: list[tuple[float, tuple[int, int, int]]] = [
        (0.40, (0, 180, 70)),
        (0.70, (50, 200, 90)),
        (1.00, (220, 180, 0)),
        (1.30, (230, 120, 30)),
        (2.00, (220, 45, 45)),
    ]

    r = float(ratio)
    if r <= stops[0][0]:
        rgb = stops[0][1]
    elif r >= stops[-1][0]:
        rgb = stops[-1][1]
    else:
        rgb = stops[-1][1]
        for i in range(len(stops) - 1):
            x0, c0 = stops[i]
            x1, c1 = stops[i + 1]
            if x0 <= r <= x1:
                t = (r - x0) / (x1 - x0) if x1 > x0 else 0.0
                rgb = (
                    int(_lerp(c0[0], c1[0], t)),
                    int(_lerp(c0[1], c1[1], t)),
                    int(_lerp(c0[2], c1[2], t)),
                )
                break

    return f"bold rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def format_pcr(ratio: float | None, *, digits: int = 2) -> Text:
    """Render a PCR value with optimistic→cautious color gradient."""
    if ratio is None:
        return Text("—", style="dim")
    return Text(f"{ratio:.{digits}f}", style=pcr_sentiment_style(ratio))


def print_pcr_legend(target: Console | None = None) -> None:
    """One-line legend for the PCR color gradient."""
    out = target or console
    legend = Text("PCR sentiment: ", style="dim")
    legend.append("low/green = optimistic (more calls)", style=pcr_sentiment_style(0.5))
    legend.append("  ·  ", style="dim")
    legend.append("≈1 amber = balanced", style=pcr_sentiment_style(1.0))
    legend.append("  ·  ", style="dim")
    legend.append("high/red = cautious (more puts)", style=pcr_sentiment_style(1.8))
    out.print(legend)


def print_header(data: OptionChainData, selected_expiries: Iterable[str]) -> None:
    expiries = list(selected_expiries)
    exp_label = ", ".join(expiries) if len(expiries) <= 3 else (
        f"{expiries[0]} … {expiries[-1]} ({len(expiries)} dates)"
    )
    body = Text()
    body.append(f"{data.symbol}", style="bold cyan")
    body.append(f"  —  {data.company_name}\n")
    body.append("Spot price: ", style="dim")
    body.append(f"{data.spot_price:,.2f} {data.currency}\n", style="bold green")
    body.append("Showing expiries: ", style="dim")
    body.append(f"{exp_label}\n")
    body.append("Fetched: ", style="dim")
    body.append(data.fetched_at.strftime("%Y-%m-%d %H:%M:%S"))
    console.print(Panel(body, title="[bold]Option Chain[/]", border_style="cyan"))


def print_glossary(verbose: bool = False) -> None:
    if not verbose:
        print_tip(
            "Call = bet stock rises · Put = bet stock falls (or hedge). "
            "Use --explain for a short glossary of every column."
        )
        return
    table = Table(title="Beginner glossary", box=box.SIMPLE, show_header=True)
    table.add_column("Term", style="cyan", no_wrap=True)
    table.add_column("What it means")
    for term, meaning in COLUMN_HELP.items():
        table.add_row(term, meaning)
    table.add_row(
        "PCR",
        "Put/Call Ratio = put activity ÷ call activity. "
        ">1 means more put activity; <1 means more call activity.",
    )
    table.add_row(
        "Expiry",
        "The last day the option is valid. After expiry it is worthless if unused.",
    )
    console.print(table)


def print_put_call_ratio(pcr: PutCallRatio, show_explain: bool = True) -> None:
    table = Table(
        title="Put / Call Ratio",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Metric")
    table.add_column("Calls", justify="right")
    table.add_column("Puts", justify="right")
    table.add_column("Put/Call Ratio", justify="right")

    table.add_row(
        "Volume (today)",
        f"{pcr.call_volume:,}",
        f"{pcr.put_volume:,}",
        format_pcr(pcr.volume_ratio, digits=3),
    )
    table.add_row(
        "Open Interest",
        f"{pcr.call_open_interest:,}",
        f"{pcr.put_open_interest:,}",
        format_pcr(pcr.oi_ratio, digits=3),
    )
    console.print(table)
    print_pcr_legend()

    if show_explain:
        console.print(f"  [dim]•[/dim] {pcr.volume_interpretation}")
        console.print(f"  [dim]•[/dim] {pcr.oi_interpretation}")
        console.print(
            "  [dim]• PCR is a sentiment snapshot, not a buy/sell signal. "
            "Always combine with other research.[/dim]"
        )


def print_expiries(
    symbol: str,
    company_name: str,
    spot_price: float,
    expiries: list[str],
) -> None:
    table = Table(
        title=f"Available option expiries — {symbol}",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold",
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("Expiry date")
    table.add_column("Days out", justify="right")

    from datetime import date

    today = date.today()
    for i, exp in enumerate(expiries, start=1):
        try:
            d = date.fromisoformat(exp)
            days = (d - today).days
            days_label = f"{days}d"
        except ValueError:
            days_label = "?"
        table.add_row(str(i), exp, days_label)

    header = f"{symbol} — {company_name}"
    if spot_price > 0:
        header += f"  |  spot {spot_price:,.2f}"
    console.print(Panel(header, border_style="cyan"))
    console.print(table)
    print_tip(
        f"Show one date:  optionchain {symbol} --expiry {expiries[0] if expiries else 'YYYY-MM-DD'}"
    )
    if len(expiries) >= 2:
        print_tip(
            f"Show a range:  optionchain {symbol} --from {expiries[0]} --to {expiries[min(2, len(expiries)-1)]}"
        )


def _fmt_money(value: float) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return f"{float(value):,.2f}"


def _fmt_iv(value: float) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    # yfinance IV is typically a decimal (0.35 = 35%)
    v = float(value)
    if v < 1e-4:
        return "—"
    pct = v * 100 if v <= 5 else v  # if already percent-like, leave it
    return f"{pct:.1f}%"


def _fmt_quote(value: float) -> str:
    """Format bid/ask; show dash when the feed has no quote (common after hours)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    v = float(value)
    if v <= 0:
        return "—"
    return f"{v:,.2f}"


def print_chain_table(
    df: pd.DataFrame,
    *,
    spot_price: float,
    option_type: str,
    max_rows: int | None = None,
) -> None:
    if df is None or df.empty:
        console.print(
            Panel(
                "No option contracts matched your filters.\n\n"
                "Try one of these:\n"
                "  • Widen strikes: drop --strike-min / --strike-max or raise --near\n"
                "  • Another expiry: --list-expiries then --expiry YYYY-MM-DD\n"
                "  • Both sides: --type all",
                title="Empty result",
                border_style="yellow",
            )
        )
        return

    display_df = df.copy()
    truncated = False
    if max_rows is not None and len(display_df) > max_rows:
        display_df = display_df.head(max_rows)
        truncated = True

    bids = display_df["bid"] if "bid" in display_df.columns else pd.Series(dtype=float)
    asks = display_df["ask"] if "ask" in display_df.columns else pd.Series(dtype=float)
    ois = (
        display_df["openInterest"]
        if "openInterest" in display_df.columns
        else pd.Series(dtype=float)
    )
    quotes_missing = bool(len(display_df)) and float(bids.fillna(0).sum() + asks.fillna(0).sum()) <= 0
    oi_missing = bool(len(display_df)) and int(ois.fillna(0).sum()) == 0

    type_label = option_type.upper() if option_type != "all" else "CALLS + PUTS"
    table = Table(
        title=f"Contracts ({type_label}) — {len(df)} row(s)",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold",
        row_styles=["", "dim"],
    )
    table.add_column("Expiry", style="cyan", no_wrap=True)
    table.add_column("Type", justify="center")
    table.add_column("Strike", justify="right")
    table.add_column("Last", justify="right")
    table.add_column("Bid", justify="right")
    table.add_column("Ask", justify="right")
    table.add_column("Vol", justify="right")
    table.add_column("OI", justify="right")
    table.add_column("IV", justify="right")
    table.add_column("ITM", justify="center")

    for _, row in display_df.iterrows():
        strike = float(row["strike"])
        # Highlight ATM-ish strikes (within ~1% of spot)
        strike_style = ""
        if spot_price > 0 and abs(strike - spot_price) / spot_price <= 0.01:
            strike_style = "bold yellow"

        opt_type = str(row.get("type", "")).lower()
        type_style = "green" if opt_type == "call" else "red"
        itm = "✓" if bool(row.get("inTheMoney")) else ""
        oi_val = int(row.get("openInterest", 0) or 0)
        oi_label = f"{oi_val:,}" if oi_val > 0 else "—"

        table.add_row(
            str(row.get("expiry", "")),
            Text(opt_type.upper(), style=type_style),
            Text(_fmt_money(strike), style=strike_style),
            _fmt_money(row.get("lastPrice", 0)),
            _fmt_quote(row.get("bid", 0)),
            _fmt_quote(row.get("ask", 0)),
            f"{int(row.get('volume', 0) or 0):,}",
            oi_label,
            _fmt_iv(row.get("impliedVolatility", 0)),
            itm,
        )

    console.print(table)
    if truncated:
        console.print(
            f"[yellow]Showing first {max_rows} of {len(df)} rows. "
            "Use --limit 0 to show all, or narrow with --near / strike filters.[/yellow]"
        )
    if spot_price > 0:
        console.print(
            f"[dim]Yellow strike ≈ at-the-money (near spot {spot_price:,.2f}). "
            "✓ = currently in the money.[/dim]"
        )
    if quotes_missing or oi_missing:
        bits = []
        if quotes_missing:
            bits.append("live Bid/Ask quotes")
        if oi_missing:
            bits.append("Open Interest")
        console.print(
            f"[dim]Note: {' and '.join(bits)} look empty right now. "
            "Yahoo often delays or omits these outside regular market hours — "
            "Last price and Volume are still useful.[/dim]"
        )



def print_summary(summary: dict[str, object]) -> None:
    if summary.get("rows", 0) == 0:
        return
    console.print(
        f"[dim]Summary: {summary['rows']} contracts · "
        f"{summary['expiries']} expir(y/ies) · "
        f"{summary['strikes']} strike(s) · "
        f"Vol {summary['total_volume']:,} · "
        f"OI {summary['total_oi']:,} · "
        f"ITM {summary['itm_count']} / OTM {summary['otm_count']}[/dim]"
    )


def print_leaders(result: LeadersResult) -> None:
    """Pretty table of underlyings ranked by options volume."""
    # Leaders tables need horizontal room; bump width when the host is narrow.
    out = Console(width=max(getattr(console, "width", 80) or 80, 120))
    n = len(result.leaders)
    header = Text()
    header.append("Top ", style="dim")
    header.append(f"{n}", style="bold cyan")
    header.append(" underlyings by options trading volume\n", style="dim")
    header.append("Source: ", style="dim")
    header.append(f"{result.source}\n")
    header.append("Scanned ", style="dim")
    header.append(f"{result.contracts_scanned:,}", style="bold")
    header.append(" most-active contracts → ", style="dim")
    header.append(f"{result.unique_underlyings}", style="bold")
    header.append(" unique underlyings\n", style="dim")
    header.append("Fetched: ", style="dim")
    header.append(result.fetched_at.strftime("%Y-%m-%d %H:%M:%S"))
    out.print(
        Panel(header, title="[bold]Options Volume Leaders[/]", border_style="magenta")
    )

    if not result.leaders:
        out.print(
            Panel(
                "No leaders to show. Try again during US market hours.",
                border_style="yellow",
            )
        )
        return

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold",
        row_styles=["", "dim"],
        expand=False,
        pad_edge=False,
    )
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Symbol", style="cyan bold", no_wrap=True)
    table.add_column("Name", no_wrap=True)
    table.add_column("Spot", justify="right", no_wrap=True)
    table.add_column("Chg%", justify="right", no_wrap=True)
    table.add_column("Opt Vol", justify="right", style="bold", no_wrap=True)
    table.add_column("Calls", justify="right", no_wrap=True)
    table.add_column("Puts", justify="right", no_wrap=True)
    table.add_column("PCR", justify="right", no_wrap=True)

    for row in result.leaders:
        if row.spot_price is None:
            spot = "—"
        else:
            spot = f"{row.spot_price:,.2f}"

        if row.change_pct is None:
            chg = Text("—")
        else:
            style = "green" if row.change_pct >= 0 else "red"
            chg = Text(f"{row.change_pct:+.2f}%", style=style)

        pcr_cell = format_pcr(row.put_call_ratio, digits=2)
        name = (row.name or row.symbol).replace("\n", " ").strip()
        if len(name) > 28:
            name = name[:27] + "…"

        table.add_row(
            str(row.rank),
            row.symbol,
            name,
            spot,
            chg,
            f"{row.options_volume:,}",
            f"{row.call_volume:,}",
            f"{row.put_volume:,}",
            pcr_cell,
        )

    out.print(table)
    print_pcr_legend(out)
    out.print(
        "[dim]Opt Vol = sum of volume across the most-active option contracts "
        "for that underlying (Yahoo ranking). Not a full exchange total for every strike.[/dim]"
    )
    if result.leaders:
        sample = result.leaders[0].symbol.lstrip("^")
        out.print(f"[dim]Tip: Drill into a chain:  optionchain {sample}[/dim]")
        out.print(
            f"[dim]Tip: Calls only for #1:  optionchain {sample} --type call[/dim]"
        )


def _change_style(pct: float | None) -> str:
    if pct is None:
        return "dim"
    if pct > 0.5:
        return "bold green"
    if pct < -0.5:
        return "bold red"
    return "yellow"


def _bucket_style(bucket: str) -> str:
    return {
        "deep_itm": "bold green",
        "itm": "green",
        "atm": "bold yellow",
        "otm": "red",
        "far_otm": "bold red",
    }.get(bucket, "")


def print_compare(result: CompareResult) -> None:
    """ITM vs OTM comparison table + plain-English research notes."""
    out = Console(width=max(getattr(console, "width", 80) or 80, 130))
    side = result.option_type.upper()
    body = Text()
    body.append(result.symbol, style="bold cyan")
    body.append(f"  —  {result.company_name}\n")
    body.append("Spot: ", style="dim")
    body.append(f"{result.spot_price:,.2f} {result.currency}", style="bold green")
    body.append("   ·   Side: ", style="dim")
    body.append(
        f"LONG {side}",
        style="bold green" if result.option_type == "call" else "bold red",
    )
    body.append("\nExpiry: ", style="dim")
    body.append(result.expiry, style="bold")
    if result.dte is not None:
        body.append(f"  ({result.dte}d to expiry)", style="dim")
    body.append("\nFetched: ", style="dim")
    body.append(result.fetched_at.strftime("%Y-%m-%d %H:%M:%S"))
    out.print(
        Panel(
            body,
            title="[bold]ITM vs OTM Compare[/]  (research aid — not advice)",
            border_style="magenta",
        )
    )

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold",
        row_styles=["", "dim"],
        pad_edge=False,
    )
    table.add_column("Style", no_wrap=True)
    table.add_column("Strike", justify="right", no_wrap=True)
    table.add_column("Moneyness", no_wrap=True)
    table.add_column("Last", justify="right", no_wrap=True)
    table.add_column("Intr.", justify="right", no_wrap=True)
    table.add_column("Extr.", justify="right", no_wrap=True)
    table.add_column("BE", justify="right", no_wrap=True)
    table.add_column("Move→BE", justify="right", no_wrap=True)
    table.add_column("$/ctr", justify="right", no_wrap=True)
    table.add_column("Lev~", justify="right", no_wrap=True)
    table.add_column("Vol", justify="right", no_wrap=True)
    table.add_column("OI", justify="right", no_wrap=True)
    if result.if_spot is not None:
        table.add_column("Worth@", justify="right", no_wrap=True)

    style_names = {
        "deep_itm": "Deep ITM",
        "itm": "ITM",
        "atm": "ATM",
        "otm": "OTM",
        "far_otm": "Far OTM",
    }

    for r in result.rows:
        style = _bucket_style(r.bucket)
        move = Text(f"{r.move_to_be_pct:+.1f}%", style=style)
        lev = "—" if r.leverage_proxy is None else f"{r.leverage_proxy:.1f}×"
        last = f"{r.last:,.2f}" if r.last > 0 else (f"{r.mid:,.2f}" if r.mid > 0 else "—")
        cells = [
            Text(style_names.get(r.bucket, r.bucket), style=style),
            f"{r.strike:,.2f}",
            Text(r.moneyness_label, style=style),
            last,
            f"{r.intrinsic:,.2f}",
            f"{r.extrinsic:,.2f}",
            f"{r.break_even:,.2f}",
            move,
            f"{r.premium_per_contract:,.0f}",
            lev,
            f"{r.volume:,}" if r.volume else "—",
            f"{r.open_interest:,}" if r.open_interest else "—",
        ]
        if result.if_spot is not None:
            worth = expiry_value_if_spot(
                result.option_type, r.strike, result.if_spot
            )
            # P/L vs premium at expiry (intrinsic - premium) rough
            prem = r.mid if r.mid > 0 else r.last
            pnl = worth - prem if prem > 0 else worth
            cells.append(
                Text(
                    f"{worth:,.2f} ({pnl:+.2f})",
                    style="green" if pnl > 0 else ("red" if pnl < 0 else "dim"),
                )
            )
        table.add_row(*cells)

    out.print(table)
    out.print(
        "[dim]Intr. = intrinsic · Extr. = time premium · BE = break-even at expiry · "
        "Move→BE = how far the stock must go · $/ctr ≈ premium×100 · "
        "Lev~ = spot/premium (rough leverage, not delta).[/dim]"
    )
    if result.if_spot is not None:
        out.print(
            f"[dim]Worth@ = intrinsic if spot finishes at {result.if_spot:,.2f}; "
            f"(…) is vs today’s premium per share.[/dim]"
        )

    out.print()
    out.print("[bold]How to read this[/bold]")
    for line in style_guidance(result):
        out.print(f"  • {line}")

    print_tip(
        f"Live chain:  optionchain {result.symbol} --expiry {result.expiry} "
        f"--type {result.option_type}"
    )
    print_tip(
        f"Tighten:  optionchain {result.symbol} --compare {result.option_type} "
        f"--target-move 5 --budget 500"
    )


def _wall_row(table: Table, wall: WallLevel | None, label: str, style: str) -> None:
    if wall is None:
        table.add_row(Text(label, style=style), "—", "—", "—", "—", "—")
        return
    table.add_row(
        Text(label, style=style),
        f"{wall.strike:,.2f}",
        f"{wall.distance_pct:+.2f}%",
        f"{wall.open_interest:,}",
        f"{100 * wall.share_of_side_oi:.1f}%",
        f"{wall.volume:,}" if wall.volume else "—",
    )


def print_walls(analysis: WallAnalysis) -> None:
    """Call wall / put wall / max pain + gamma-style evaluation."""
    out = Console(width=max(getattr(console, "width", 80) or 80, 110))
    body = Text()
    body.append(analysis.symbol, style="bold cyan")
    body.append(f"  —  {analysis.company_name}\n")
    body.append("Spot: ", style="dim")
    body.append(f"{analysis.spot_price:,.2f} {analysis.currency}", style="bold green")
    body.append("\nExpiry: ", style="dim")
    body.append(analysis.expiry, style="bold")
    if analysis.dte is not None:
        body.append(f"  ({analysis.dte}d to expiry)", style="dim")
    body.append("\nFetched: ", style="dim")
    body.append(analysis.fetched_at.strftime("%Y-%m-%d %H:%M:%S"))
    out.print(
        Panel(
            body,
            title="[bold]Call Wall · Put Wall · Max Pain[/]  (gamma-style research)",
            border_style="magenta",
        )
    )

    # Headline levels
    levels = Table(
        title="Key levels",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
    )
    levels.add_column("Level")
    levels.add_column("Strike", justify="right")
    levels.add_column("vs Spot", justify="right")
    levels.add_column("OI", justify="right")
    levels.add_column("% of side OI", justify="right")
    levels.add_column("Vol", justify="right")

    _wall_row(levels, analysis.put_wall, "Put wall (support)", "bold red")
    _wall_row(levels, analysis.call_wall, "Call wall (resistance)", "bold green")
    if analysis.max_pain is not None:
        dist = (
            100.0 * (analysis.max_pain - analysis.spot_price) / analysis.spot_price
            if analysis.spot_price
            else 0.0
        )
        levels.add_row(
            Text("Max pain", style="bold yellow"),
            f"{analysis.max_pain:,.2f}",
            f"{dist:+.2f}%",
            "—",
            "—",
            "—",
        )
    out.print(levels)

    if (
        analysis.pin_range_low is not None
        and analysis.pin_range_high is not None
    ):
        inside = analysis.spot_in_pin_range
        pin_style = "bold cyan" if inside else "dim"
        out.print(
            Text(
                f"  Pin / gamma range:  {analysis.pin_range_low:g}  →  "
                f"{analysis.pin_range_high:g}   "
                f"({'spot INSIDE range' if inside else 'spot OUTSIDE range'})",
                style=pin_style,
            )
        )

    out.print(
        f"[dim]  Total OI — calls {analysis.total_call_oi:,} · "
        f"puts {analysis.total_put_oi:,}   ·   "
        f"Volume — calls {analysis.total_call_vol:,} · "
        f"puts {analysis.total_put_vol:,}[/dim]"
    )

    # Top walls tables side concept
    for title, walls, style in (
        ("Top call OI strikes", analysis.top_call_walls, "green"),
        ("Top put OI strikes", analysis.top_put_walls, "red"),
    ):
        if not walls:
            continue
        t = Table(title=title, box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        t.add_column("#", justify="right", style="dim")
        t.add_column("Strike", justify="right", style=style)
        t.add_column("vs Spot", justify="right")
        t.add_column("OI", justify="right")
        t.add_column("% side", justify="right")
        t.add_column("Vol", justify="right")
        for w in walls:
            t.add_row(
                str(w.rank),
                f"{w.strike:,.2f}",
                f"{w.distance_pct:+.2f}%",
                f"{w.open_interest:,}",
                f"{100 * w.share_of_side_oi:.1f}%",
                f"{w.volume:,}" if w.volume else "—",
            )
        out.print(t)

    out.print()
    out.print("[bold magenta]Gamma-style evaluation[/bold magenta]")
    for line in analysis.evaluation:
        # simple bold markers
        text = line.replace("**", "")
        out.print(f"  • {text}")

    print_tip(
        f"Chain view:  optionchain {analysis.symbol} --expiry {analysis.expiry}"
    )
    print_tip(
        f"ITM vs OTM:  optionchain {analysis.symbol} --compare call "
        f"--expiry {analysis.expiry}"
    )


def print_chain_history(result: ChainHistoryResult) -> None:
    """Table of option closes over recent trading days + period change."""
    out = Console(width=max(getattr(console, "width", 80) or 80, 120))
    dates = result.trade_dates
    date_labels = [d.strftime("%m/%d") for d in dates]

    body = Text()
    body.append(result.symbol, style="bold cyan")
    body.append(f"  —  {result.company_name}\n")
    body.append("Spot now: ", style="dim")
    body.append(f"{result.spot_price:,.2f} {result.currency}", style="bold green")
    if result.spot_change is not None and result.spot_change_pct is not None:
        body.append("   ·   stock over window: ", style="dim")
        body.append(
            f"{result.spot_change:+.2f} ({result.spot_change_pct:+.2f}%)",
            style=_change_style(result.spot_change_pct),
        )
    body.append("\nExpiry: ", style="dim")
    body.append(result.expiry, style="bold")
    body.append("   ·   sessions: ", style="dim")
    body.append(
        f"{len(dates)} (requested {result.days_requested})",
        style="bold",
    )
    body.append("\nFetched: ", style="dim")
    body.append(result.fetched_at.strftime("%Y-%m-%d %H:%M:%S"))
    out.print(
        Panel(body, title="[bold]Option Chain — Change Over Days[/]", border_style="cyan")
    )

    if not dates:
        out.print(
            Panel(
                "No trading sessions found in the history window.",
                border_style="yellow",
            )
        )
        return

    # Spot row for context
    spot_bits = []
    for d in dates:
        px = result.spot_by_date.get(d)
        spot_bits.append(f"{px:,.2f}" if px is not None else "—")
    out.print(
        "[dim]Underlying closes:[/dim]  "
        + "  →  ".join(
            f"[cyan]{lab}[/cyan] {val}" for lab, val in zip(date_labels, spot_bits)
        )
    )

    table = Table(
        title=f"Contract closes ({len(result.contracts)} options)",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold",
        row_styles=["", "dim"],
        pad_edge=False,
    )
    table.add_column("Type", justify="center", no_wrap=True)
    table.add_column("Strike", justify="right", no_wrap=True)
    for lab in date_labels:
        table.add_column(lab, justify="right", no_wrap=True)
    table.add_column("Δ $", justify="right", no_wrap=True)
    table.add_column("Δ %", justify="right", no_wrap=True)
    table.add_column("Vol (last)", justify="right", no_wrap=True)

    for c in result.contracts:
        otype = c.option_type.upper()
        type_style = "green" if c.option_type == "call" else "red"
        cells: list = [
            Text(otype, style=type_style),
            f"{c.strike:,.2f}",
        ]
        for d in dates:
            px = c.close_on(d)
            cells.append(f"{px:,.2f}" if px is not None else "—")

        d_dollar = c.dollar_change
        d_pct = c.percent_change
        style = _change_style(d_pct)
        if d_dollar is None:
            cells.append(Text("—", style="dim"))
        else:
            cells.append(Text(f"{d_dollar:+.2f}", style=style))
        if d_pct is None:
            cells.append(Text("—", style="dim"))
        else:
            cells.append(Text(f"{d_pct:+.1f}%", style=style))

        last_vol = c.points[-1].volume if c.points else 0
        cells.append(f"{last_vol:,}" if last_vol else "—")
        table.add_row(*cells)

    out.print(table)
    out.print(
        "[dim]Green Δ = option got more expensive over the window · "
        "Red Δ = cheaper. Prices are daily closes from Yahoo; "
        "thin contracts may skip sessions (shown as —).[/dim]"
    )
    out.print(
        f"[dim]Tip: Live chain:  optionchain {result.symbol} --expiry {result.expiry}[/dim]"
    )
    out.print(
        f"[dim]Tip: Calls only history:  optionchain {result.symbol} "
        f"--history {result.days_requested} --type call[/dim]"
    )
