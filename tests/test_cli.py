"""CLI argument validation and help smoke tests (no network)."""

from __future__ import annotations

import pytest

from optionchain.cli import build_parser, run
from optionchain.fetcher import OptionChainError
from optionchain.cli import _validate_args


def test_help_exits_zero():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0


def test_version_exits_zero():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0


def test_missing_symbol_returns_error_code():
    code = run([])
    assert code == 1


def test_validate_conflicting_expiry_and_range():
    parser = build_parser()
    args = parser.parse_args(
        ["TSLA", "--expiry", "2026-07-18", "--from", "2026-07-01"]
    )
    with pytest.raises(OptionChainError, match="either --expiry"):
        _validate_args(args)


def test_validate_conflicting_expiry_and_nearest():
    parser = build_parser()
    args = parser.parse_args(["TSLA", "--expiry", "2026-07-18", "--nearest", "2"])
    with pytest.raises(OptionChainError, match="either --expiry or --nearest"):
        _validate_args(args)


def test_validate_bad_strike_order():
    parser = build_parser()
    args = parser.parse_args(
        ["TSLA", "--strike-min", "200", "--strike-max", "100"]
    )
    with pytest.raises(OptionChainError, match="strike-min"):
        _validate_args(args)


def test_validate_ok_defaults():
    parser = build_parser()
    args = parser.parse_args(["tsla", "--type", "call", "--near", "5"])
    _validate_args(args)  # should not raise
    assert args.option_type == "call"
    assert args.near == 5


def test_parser_accepts_aliases():
    parser = build_parser()
    args = parser.parse_args(["SPY", "--put-call-ratio", "--type", "put"])
    assert args.show_pcr is True
    assert args.option_type == "put"


def test_parser_all_strikes_flag():
    parser = build_parser()
    args = parser.parse_args(["TSLA", "--all-strikes", "--no-pcr"])
    assert args.all_strikes is True
    assert args.no_pcr is True
