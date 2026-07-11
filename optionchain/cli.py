"""Command-line interface for OptionChain."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from optionchain import __app_name__, __version__
from optionchain.compare import compare_itm_otm
from optionchain.display import (
    print_chain_history,
    print_chain_table,
    print_compare,
    print_error,
    print_expiries,
    print_glossary,
    print_header,
    print_leaders,
    print_put_call_ratio,
    print_summary,
    print_tip,
)
from optionchain.fetcher import OptionChainError, fetch_option_chain, list_expiries
from optionchain.filters import apply_filters, select_expiries
from optionchain.history import fetch_chain_history
from optionchain.leaders import TOP_COMMANDS, fetch_option_volume_leaders
from optionchain.metrics import compute_put_call_ratio, summarize_chain
from optionchain.plotting import print_terminal_plot, save_chain_history_plot


EXAMPLES = """
examples:
  optionchain top
      Top 20 stocks/ETFs by options trading volume today

  optionchain top -n 10
      Top 10 only

  optionchain TSLA
      Show the nearest expiry option chain for Tesla

  optionchain TSLA --history 5
      How near-the-money options moved over the last ~5 sessions

  optionchain SPY --history 5 --plot
      History table + call/put chart drawn in the terminal

  optionchain SPY --history 5 --plot --save
      Terminal chart + save a PNG (auto filename)

  optionchain TSLA --history 5 --plot --save ./tsla.png
      Terminal chart + save to a path you choose

  optionchain SPY --history 7 --type call --near 4
      7-day call price path around the stock price

  optionchain AAPL --type call
      Calls only

  optionchain SPY --type put --pcr
      Puts only, and always print the put/call ratio

  optionchain TSLA --list-expiries
      List every available expiration date

  optionchain NVDA --expiry 2026-07-17
      One specific expiry date

  optionchain MSFT --from 2026-07-01 --to 2026-09-30
      All expiries in a date range

  optionchain TSLA --near 5 --explain
      5 strikes closest to the current price + beginner glossary

  optionchain AMZN --nearest 3 --type all --limit 50
      Next 3 expiries, both calls and puts, cap output at 50 rows

  optionchain TSLA --compare call
      ITM vs ATM vs OTM long-call styles for the nearest expiry

  optionchain SPY --compare put --target-move 3 --budget 400
      Long puts that can break even on ~3% down, max $400/contract

  optionchain NVDA --compare call --if-spot 220 --expiry 2026-08-15
      Compare styles + intrinsic value if NVDA finishes at 220
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=__app_name__,
        description=(
            "Show the latest stock option chain in your terminal.\n"
            "Also:  optionchain top  → stocks with the highest options volume."
        ),
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "symbol",
        nargs="?",
        help=(
            "Stock ticker (e.g. TSLA, AAPL, SPY), or a special command: "
            "top / leaders / hot (most options volume)."
        ),
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=20,
        metavar="N",
        help="For 'top': how many underlyings to show (default: 20, max: 100).",
    )
    parser.add_argument(
        "-t",
        "--type",
        dest="option_type",
        default="all",
        choices=["call", "put", "all"],
        help="Show only calls, only puts, or both (default: all).",
    )
    parser.add_argument(
        "-e",
        "--expiry",
        help="Single expiry date to show (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--from",
        dest="expiry_from",
        help="Start of expiry date range (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--to",
        dest="expiry_to",
        help="End of expiry date range (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--nearest",
        type=int,
        default=None,
        metavar="N",
        help="Show the nearest N expiry dates (default without range: 1).",
    )
    parser.add_argument(
        "--list-expiries",
        action="store_true",
        help="List all available option expiry dates for the symbol and exit.",
    )
    parser.add_argument(
        "--pcr",
        "--put-call-ratio",
        action="store_true",
        dest="show_pcr",
        help="Show put/call ratio (also shown by default unless --no-pcr).",
    )
    parser.add_argument(
        "--no-pcr",
        action="store_true",
        help="Hide the put/call ratio section.",
    )
    parser.add_argument(
        "--strike-min",
        type=float,
        default=None,
        metavar="PRICE",
        help="Only show strikes at or above this price.",
    )
    parser.add_argument(
        "--strike-max",
        type=float,
        default=None,
        metavar="PRICE",
        help="Only show strikes at or below this price.",
    )
    parser.add_argument(
        "--near",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Only show the N strikes closest to the current stock price "
            "(per expiry/type). Default: 8 when no strike filter is set."
        ),
    )
    parser.add_argument(
        "--all-strikes",
        action="store_true",
        help="Show every strike (disables the default near-the-money focus).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=40,
        metavar="N",
        help="Max chain rows to print (default: 40). Use 0 for no limit.",
    )
    parser.add_argument(
        "--history",
        "--change-over",
        type=int,
        default=None,
        metavar="DAYS",
        dest="history_days",
        help=(
            "Show how the option chain changed over the last DAYS trading sessions "
            "(daily closes for near-the-money contracts). Example: --history 5"
        ),
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help=(
            "With --history: draw call (green) and put (red) prices as a chart "
            "directly in the terminal."
        ),
    )
    parser.add_argument(
        "--save",
        "--plot-path",
        nargs="?",
        const="__AUTO__",
        default=None,
        metavar="FILE",
        dest="save_path",
        help=(
            "Optional: also save a high-res PNG of the chart. "
            "Use --save alone for an auto filename, or --save ./chart.png for a path. "
            "Implies --plot."
        ),
    )
    parser.add_argument(
        "--compare",
        choices=["call", "put"],
        default=None,
        metavar="SIDE",
        help=(
            "ITM vs OTM research table for a long call or long put "
            "(one expiry). Shows intrinsic/extrinsic, break-even, move needed."
        ),
    )
    parser.add_argument(
        "--target-move",
        type=float,
        default=None,
        metavar="PCT",
        help=(
            "With --compare: only keep strikes that can break even if the stock "
            "moves about this percent (e.g. 5 for 5%%)."
        ),
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        metavar="USD",
        help=(
            "With --compare: max premium dollars for one contract "
            "(premium × 100). Example: --budget 500"
        ),
    )
    parser.add_argument(
        "--if-spot",
        type=float,
        default=None,
        metavar="PRICE",
        help=(
            "With --compare: show each strike's intrinsic value if the stock "
            "finishes at this price at expiry."
        ),
    )
    parser.add_argument(
        "--all-styles",
        action="store_true",
        help=(
            "With --compare: show many strikes near the money instead of one "
            "pick per ITM/ATM/OTM style bucket."
        ),
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Print a short beginner glossary of option terms and columns.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _is_top_command(symbol: str | None) -> bool:
    if not symbol:
        return False
    return symbol.strip().lower() in TOP_COMMANDS


def _validate_args(args: argparse.Namespace) -> None:
    if not args.symbol:
        raise OptionChainError(
            "Missing stock ticker or command.\n\n"
            "  Usage:  optionchain TICKER [options]\n"
            "  Usage:  optionchain top [-n 20]\n"
            "  Usage:  optionchain TSLA --compare call\n"
            "  Example: optionchain TSLA\n"
            "  Example: optionchain top\n"
            "  Example: optionchain AAPL --type call --near 8\n\n"
            "Run  optionchain --help  for every option."
        )

    if _is_top_command(args.symbol):
        if args.count < 1:
            raise OptionChainError("--count / -n must be at least 1.")
        if args.count > 100:
            raise OptionChainError("--count / -n cannot exceed 100.")
        return

    if args.compare is not None:
        if args.history_days is not None:
            raise OptionChainError("Use either --compare or --history, not both.")
        if args.expiry_from or args.expiry_to or args.nearest is not None:
            raise OptionChainError(
                "--compare uses a single expiry. "
                "Pass --expiry YYYY-MM-DD or omit for the nearest."
            )
        if args.target_move is not None and args.target_move <= 0:
            raise OptionChainError("--target-move must be positive (e.g. 5).")
        if args.budget is not None and args.budget <= 0:
            raise OptionChainError("--budget must be a positive dollar amount.")
        if args.if_spot is not None and args.if_spot <= 0:
            raise OptionChainError("--if-spot must be a positive price.")

    if args.expiry and (args.expiry_from or args.expiry_to):
        raise OptionChainError(
            "Use either --expiry for one date, or --from/--to for a range — not both."
        )

    if args.expiry and args.nearest is not None:
        raise OptionChainError(
            "Use either --expiry or --nearest, not both."
        )

    if args.nearest is not None and args.nearest < 1:
        raise OptionChainError("--nearest must be at least 1.")

    if args.near is not None and args.near < 1:
        raise OptionChainError("--near must be at least 1.")

    if args.plot or args.save_path is not None:
        # --save implies --plot (terminal chart + optional file)
        args.plot = True
        if args.history_days is None:
            raise OptionChainError(
                "--plot / --save only work with --history.\n"
                "  Example:  optionchain TSLA --history 5 --plot\n"
                "  Example:  optionchain TSLA --history 5 --plot --save"
            )

    if args.history_days is not None:
        if args.history_days < 1:
            raise OptionChainError("--history DAYS must be at least 1.")
        if args.history_days > 30:
            raise OptionChainError("--history supports at most 30 days.")
        if args.expiry_from or args.expiry_to or args.nearest is not None:
            raise OptionChainError(
                "--history uses a single expiry. "
                "Pass --expiry YYYY-MM-DD (or omit for the nearest), "
                "not --from/--to/--nearest."
            )

    if args.limit < 0:
        raise OptionChainError("--limit cannot be negative. Use 0 for unlimited.")

    if (
        args.strike_min is not None
        and args.strike_max is not None
        and args.strike_min > args.strike_max
    ):
        raise OptionChainError(
            f"--strike-min ({args.strike_min}) is greater than "
            f"--strike-max ({args.strike_max})."
        )


def _run_top(args: argparse.Namespace) -> int:
    result = fetch_option_volume_leaders(top_n=args.count)
    print_leaders(result)
    if args.explain:
        print_glossary(verbose=True)
    else:
        print_tip(
            "PCR here uses call/put volume from the most-active contracts only."
        )
    return 0


def _run_compare(args: argparse.Namespace) -> int:
    result = compare_itm_otm(
        args.symbol,
        option_type=args.compare,
        expiry=args.expiry,
        target_move_pct=args.target_move,
        budget=args.budget,
        if_spot=args.if_spot,
        style_picks_only=not args.all_styles,
    )
    # When showing many strikes, keep a readable near-ATM band
    if args.all_styles:
        near = args.near if args.near is not None else 12
        spot = result.spot_price
        ranked = sorted(result.rows, key=lambda r: abs(r.strike - spot))[: max(near * 2, 8)]
        result.rows = sorted(ranked, key=lambda r: r.strike)
    print_compare(result)
    if args.explain:
        print_glossary(verbose=True)
    return 0


def _run_history(args: argparse.Namespace) -> int:
    near = args.near if args.near is not None else 6
    result = fetch_chain_history(
        args.symbol,
        days=args.history_days,
        expiry=args.expiry,
        option_type=args.option_type,
        near=near,
        strike_min=args.strike_min,
        strike_max=args.strike_max,
    )
    print_chain_history(result)

    if args.plot:
        # Always show in-terminal first
        print_terminal_plot(result)
        # Optional high-res file
        if args.save_path is not None:
            file_path = None if args.save_path == "__AUTO__" else args.save_path
            saved = save_chain_history_plot(result, path=file_path)
            print_tip(f"Chart saved to: {saved}")
        else:
            print_tip(
                f"Save this chart:  optionchain {result.symbol} "
                f"--history {result.days_requested} --plot --save"
            )

    if args.explain:
        print_glossary(verbose=True)
    return 0


def _run_chain(args: argparse.Namespace) -> int:
    if args.list_expiries:
        symbol, name, spot, expiries = list_expiries(args.symbol)
        print_expiries(symbol, name, spot, expiries)
        if args.explain:
            print_glossary(verbose=True)
        return 0

    if args.compare is not None:
        return _run_compare(args)

    if args.history_days is not None:
        return _run_history(args)

    symbol, _name, spot, available = list_expiries(args.symbol)
    selected = select_expiries(
        available,
        expiry=args.expiry,
        expiry_from=args.expiry_from,
        expiry_to=args.expiry_to,
        nearest=args.nearest,
    )

    data = fetch_option_chain(symbol, expiries=selected)
    if data.spot_price > 0:
        spot = data.spot_price

    print_header(data, selected)

    if not args.no_pcr:
        pcr = compute_put_call_ratio(data.calls, data.puts)
        print_put_call_ratio(pcr, show_explain=True)

    near = args.near
    if args.all_strikes:
        near = None
    elif near is None and args.strike_min is None and args.strike_max is None:
        near = 8

    filtered = apply_filters(
        data.calls,
        data.puts,
        option_type=args.option_type,
        strike_min=args.strike_min,
        strike_max=args.strike_max,
        spot_price=spot,
        near=near,
    )

    max_rows = None if args.limit == 0 else args.limit
    print_chain_table(
        filtered,
        spot_price=spot,
        option_type=args.option_type,
        max_rows=max_rows,
    )
    print_summary(summarize_chain(filtered, spot))

    if args.explain:
        print_glossary(verbose=True)
    else:
        print_glossary(verbose=False)
        print_tip(f"More dates: optionchain {data.symbol} --list-expiries")
        if near is not None:
            print_tip(
                f"See every strike: optionchain {data.symbol} --all-strikes --limit 0"
            )
        print_tip("See today's busiest options names: optionchain top")
        print_tip(
            f"Change over days: optionchain {data.symbol} --history 5"
        )
        print_tip(
            f"ITM vs OTM: optionchain {data.symbol} --compare call"
        )

    return 0


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        _validate_args(args)
        if _is_top_command(args.symbol):
            return _run_top(args)
        return _run_chain(args)
    except OptionChainError as exc:
        print_error(str(exc))
        return 1
    except KeyboardInterrupt:
        print_error("Cancelled.")
        return 130
    except Exception as exc:  # pragma: no cover - safety net
        print_error(
            f"Unexpected error: {exc}\n"
            "If this keeps happening, check your internet connection "
            "or try again in a minute (market data providers rate-limit)."
        )
        return 2


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
