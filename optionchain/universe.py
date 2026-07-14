"""Curated liquid big-cap / mega-ETF universe for quick picker UI."""

from __future__ import annotations

# (symbol, short label) — liquid US names with active option markets
BIG_CAPS: list[tuple[str, str]] = [
    # Index / mega ETFs
    ("SPY", "S&P 500 ETF"),
    ("QQQ", "Nasdaq 100 ETF"),
    ("IWM", "Russell 2000 ETF"),
    ("DIA", "Dow ETF"),
    # Mega-cap tech
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("NVDA", "NVIDIA"),
    ("GOOGL", "Alphabet"),
    ("AMZN", "Amazon"),
    ("META", "Meta"),
    ("TSLA", "Tesla"),
    ("AVGO", "Broadcom"),
    ("AMD", "AMD"),
    ("NFLX", "Netflix"),
    ("ORCL", "Oracle"),
    ("CRM", "Salesforce"),
    ("ADBE", "Adobe"),
    ("INTC", "Intel"),
    ("CSCO", "Cisco"),
    # Finance
    ("JPM", "JPMorgan"),
    ("BAC", "Bank of America"),
    ("GS", "Goldman Sachs"),
    ("MS", "Morgan Stanley"),
    ("V", "Visa"),
    ("MA", "Mastercard"),
    ("BRK-B", "Berkshire B"),
    # Consumer / retail
    ("WMT", "Walmart"),
    ("COST", "Costco"),
    ("HD", "Home Depot"),
    ("MCD", "McDonald's"),
    ("NKE", "Nike"),
    ("SBUX", "Starbucks"),
    ("DIS", "Disney"),
    # Healthcare
    ("UNH", "UnitedHealth"),
    ("JNJ", "Johnson & Johnson"),
    ("LLY", "Eli Lilly"),
    ("PFE", "Pfizer"),
    ("ABBV", "AbbVie"),
    ("MRK", "Merck"),
    # Energy / industrial
    ("XOM", "Exxon"),
    ("CVX", "Chevron"),
    ("CAT", "Caterpillar"),
    ("BA", "Boeing"),
    ("GE", "GE"),
    # Other liquid mega
    ("KO", "Coca-Cola"),
    ("PEP", "Pepsi"),
    ("PG", "Procter & Gamble"),
    ("T", "AT&T"),
    ("VZ", "Verizon"),
    ("IBM", "IBM"),
    ("UBER", "Uber"),
    ("PLTR", "Palantir"),
    ("COIN", "Coinbase"),
    ("SMCI", "Super Micro"),
]

BIG_CAP_SYMBOLS: list[str] = [s for s, _ in BIG_CAPS]
BIG_CAP_LABELS: dict[str, str] = {s: n for s, n in BIG_CAPS}


def big_cap_choices() -> list[str]:
    """Dropdown labels: 'AAPL — Apple'."""
    return [f"{sym} — {name}" for sym, name in BIG_CAPS]


def parse_big_cap_choice(choice: str) -> str | None:
    """Extract ticker from a choice string or raw symbol."""
    text = (choice or "").strip()
    if not text:
        return None
    if "—" in text:
        text = text.split("—", 1)[0].strip()
    elif " - " in text:
        text = text.split(" - ", 1)[0].strip()
    sym = text.upper().replace(" ", "")
    return sym or None
