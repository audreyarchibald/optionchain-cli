# optionchain-cli

A beginner-friendly terminal tool that shows the **latest option chain** for any stock ticker.

Type a symbol like `TSLA` and instantly see calls, puts, expiries, and the put/call ratio — with plain-English tips so stock-market newbies can follow along.

Data is pulled live from Yahoo Finance via [`yfinance`](https://github.com/ranaroussi/yfinance).

Managed with **[uv](https://docs.astral.sh/uv/)** — no manual `venv` / `pip` needed.

---

## Setup (once)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if you don’t have it, then:

```bash
cd optionchain-cli
uv sync
```

That creates `.venv` for you and installs the app + deps (including pytest).

---

## Run

Always prefix with `uv run` — no `source .venv/bin/activate`:

```bash
uv run optionchain top              # top 20 by options volume
uv run optionchain TSLA
uv run optionchain AAPL --type call
uv run optionchain --help
```

### Optional: install as a global command

```bash
uv tool install .
optionchain TSLA    # works from any directory
optionchain-gui     # desktop GUI
```

### Desktop GUI

```bash
uv run optionchain-gui
```

Tabs mirror the CLI: **Option Chain**, **Top Volume** (+ TradingView export), **History + Chart** (interactive plot), **ITM vs OTM**.

#### Build a standalone app binary (macOS / Windows / Linux)

```bash
chmod +x scripts/build_gui.sh
./scripts/build_gui.sh
```

- **macOS:** `open dist/OptionChain.app`
- **Linux/Windows:** run `dist/OptionChain` (or `OptionChain.exe`)

Needs internet at runtime (live Yahoo data).

---

## Quick start (copy & paste)

```bash
# Top 20 stocks/ETFs with the highest options trading volume
uv run optionchain top

# Top 10 only
uv run optionchain top -n 10

# Top 30 + export a TradingView watchlist .txt
uv run optionchain top -n 30 --export
uv run optionchain top -n 30 --export ./tv_watchlist.txt

# Same thing (aliases): leaders | hot | most-active
uv run optionchain leaders

# How near-the-money options moved over the last ~5 trading days
uv run optionchain TSLA --history 5
uv run optionchain SPY --history 7 --type call --near 4

# History + call/put chart drawn in the terminal
uv run optionchain SPY --history 5 --plot

# Terminal chart + save a PNG (auto name, or pick a path)
uv run optionchain SPY --history 5 --plot --save
uv run optionchain TSLA --history 5 --plot --save ./tsla.png

# Nearest expiry, strikes near the stock price, put/call ratio
uv run optionchain TSLA

# Calls only
uv run optionchain AAPL --type call

# Puts only + plain-English glossary
uv run optionchain SPY --type put --explain

# List every available expiry date
uv run optionchain NVDA --list-expiries

# One specific expiry
uv run optionchain TSLA --expiry 2026-08-21

# All expiries inside a date range
uv run optionchain MSFT --from 2026-07-01 --to 2026-09-30

# Next 3 expiries, 5 strikes around the stock price
uv run optionchain AMZN --nearest 3 --near 5

# Every strike (can be long) without row cap
uv run optionchain META --all-strikes --limit 0

# Hide put/call ratio
uv run optionchain GOOG --no-pcr

# ITM vs OTM research (long call or long put)
uv run optionchain TSLA --compare call
uv run optionchain SPY --compare put --target-move 3 --budget 400
uv run optionchain NVDA --compare call --if-spot 220

# Call wall / put wall / max pain (gamma-style evaluation)
uv run optionchain TSLA --walls
uv run optionchain SPY --walls --expiry 2026-07-18 --top-walls 8
```

---

## What the columns mean

| Column | Plain English |
|--------|----------------|
| **Spot price** | Latest stock price |
| **Strike** | Price where the option can be exercised |
| **Last** | Most recent trade price of that option |
| **Bid / Ask** | Current buy / sell quotes (often empty after hours) |
| **Vol** | Contracts traded **today** |
| **OI** | Open interest — contracts still open |
| **IV** | Implied volatility — market’s expected move |
| **ITM** | In the money (already has intrinsic value) |
| **PCR** | Put/Call ratio = put activity ÷ call activity |

- **Yellow strikes** ≈ at-the-money (close to the stock price)
- **Green CALL / red PUT** for quick scanning
- Default view focuses on **~8 strikes near the stock price** so the table stays readable

---

## TradingView watchlist export

```bash
uv run optionchain top -n 30 --export
# or
uv run optionchain top -n 30 --export ./my_watchlist.txt
```

Creates a text file (one symbol per line, e.g. `NASDAQ:AAPL`) you can import in TradingView:

1. Open a **Watchlist**
2. Click the **···** menu
3. **Import list of symbols**
4. Choose the `.txt` file

Use `--no-exchange` if you prefer bare tickers without the `NASDAQ:` / `NYSE:` prefix.

---

## Call wall / put wall / max pain (`--walls`)

```bash
uv run optionchain TSLA --walls
uv run optionchain SPY --gamma          # alias
```

| Level | Definition (research convention) |
|-------|----------------------------------|
| **Call wall** | Highest **call OI** at or above spot (fallback: max call OI) |
| **Put wall** | Highest **put OI** at or below spot (fallback: max put OI) |
| **Max pain** | Strike minimizing total option holder value at expiry |
| **Pin range** | Band between put wall and call wall |

Also prints top OI strikes per side and a plain-English **gamma-style evaluation** (positioning notes — not a trade signal). Yahoo OI can be delayed/incomplete.

---

## ITM vs OTM (`--compare`)

When you are deciding between **in-the-money** and **out-of-the-money** contracts for a **long** call or put:

```bash
uv run optionchain TSLA --compare call
```

You get one representative strike for each style (deep ITM → far OTM) with:

| Column | Meaning |
|--------|---------|
| **Moneyness** | How far ITM/OTM as % of spot |
| **Intr. / Extr.** | Intrinsic value vs time premium |
| **BE / Move→BE** | Break-even price and % stock move needed |
| **$/ctr** | Approx. dollars for one contract |
| **Lev~** | Rough leverage proxy (spot ÷ premium) |
| **Vol / OI** | Liquidity clues |

Optional filters: `--target-move 5`, `--budget 500`, scenario `--if-spot 450`.

This is a **research aid**, not a buy/sell recommendation.

---

## Put/Call ratio (30-second primer)

| PCR (volume or OI) | Rough reading |
|--------------------|---------------|
| **&lt; 0.7** | More call activity → often bullish-leaning |
| **0.7 – 1.0** | Fairly balanced |
| **&gt; 1.0** | More put activity → cautious / hedging-leaning |

This is a **sentiment snapshot**, not a buy/sell signal.

---

## All CLI flags

```
usage: optionchain [-h] [-t {call,put,all}] [-e EXPIRY]
                   [--from DATE] [--to DATE] [--nearest N]
                   [--list-expiries] [--pcr] [--no-pcr]
                   [--strike-min PRICE] [--strike-max PRICE]
                   [--near N] [--all-strikes] [--limit N]
                   [--explain] [-v]
                   [symbol]
```

| Flag | Description |
|------|-------------|
| `symbol` | Ticker (`TSLA`, `AAPL`, …) **or** `top` / `leaders` / `hot` |
| `-n / --count` | For `top`: how many underlyings to list (default **20**) |
| `--export [FILE]` | With `top`: write a TradingView watchlist `.txt` (auto name or path) |
| `--no-exchange` | With `--export`: bare tickers only (`AAPL` not `NASDAQ:AAPL`) |
| `--history DAYS` | Multi-day change for near-ATM contracts (daily closes, max 30) |
| `--plot` | With `--history`: draw call/put chart **in the terminal** |
| `--save [FILE]` | Also save a PNG (auto name, or your path). Implies `--plot` |
| `--compare call\|put` | ITM vs ATM vs OTM styles for a long call/put |
| `--walls` / `--gamma` | Call wall, put wall, max pain + evaluation |
| `--top-walls N` | With `--walls`: top N OI strikes per side (default 5) |
| `--target-move PCT` | With `--compare`: keep strikes that can break even on ~PCT move |
| `--budget USD` | With `--compare`: max $ premium per contract (×100) |
| `--if-spot PRICE` | With `--compare`: intrinsic if stock finishes at PRICE |
| `--all-styles` | With `--compare`: more strikes near ATM, not one per bucket |
| `-t / --type` | `call`, `put`, or `all` (default) |
| `-e / --expiry` | Single expiry `YYYY-MM-DD` |
| `--from` / `--to` | Expiry date range (`YYYY-MM-DD`) |
| `--nearest N` | Soonest N expiries (default without range: **1**) |
| `--list-expiries` | Print available expiries and exit |
| `--pcr` | Show put/call ratio (already on by default) |
| `--no-pcr` | Hide put/call ratio |
| `--strike-min` / `--strike-max` | Filter by strike price |
| `--near N` | N strikes closest to spot (default **8**) |
| `--all-strikes` | Show every strike (turns off the near-ATM default) |
| `--limit N` | Max rows printed (default **40**; `0` = unlimited) |
| `--explain` | Beginner glossary of every term |
| `-v / --version` | Print version |

---

## Tests

```bash
uv run pytest -q
```

Unit tests cover filters, metrics, date parsing, and CLI validation (no network).

---

## uv cheat sheet

| Goal | Command |
|------|---------|
| Install / refresh deps | `uv sync` |
| Run the CLI | `uv run optionchain TSLA` |
| Run tests | `uv run pytest` |
| Add a dependency | `uv add some-package` |
| Add a dev dependency | `uv add --dev some-package` |
| Install globally | `uv tool install .` |

---

## Notes & limits

- Needs **internet** (live market data).
- Outside regular US market hours, Yahoo often omits **Bid/Ask** and some **Open Interest**; **Last** and **Volume** usually still work. The CLI calls this out when it happens.
- **Not financial advice.** For personal research and learning only. Options can expire worthless; you can lose 100% of the premium paid.
- Wrong tickers get a clear error (e.g. use `TSLA`, not `TESLA`).
- Data via Yahoo Finance / `yfinance` — free delayed/research-quality, not a professional OPRA feed.

---

## Project layout

```
optionchain-cli/
├── optionchain/
│   ├── cli.py        # argparse entrypoint
│   ├── fetcher.py    # Yahoo Finance download
│   ├── leaders.py    # top underlyings by options volume
│   ├── history.py    # multi-day option price paths
│   ├── compare.py    # ITM vs OTM research compare
│   ├── plotting.py   # terminal + PNG charts
│   ├── filters.py    # type / expiry / strike filters
│   ├── metrics.py    # put/call ratio + summaries
│   └── display.py    # rich terminal tables
├── tests/
├── pyproject.toml
├── uv.lock
└── README.md
```
