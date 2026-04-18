"""
Russell 1000 + 2000 universe loader.

Primary source: iShares IWM (Russell 2000) and IWB (Russell 1000) holdings CSV.
These are free, public, updated daily by BlackRock.

Fallback: Cached baked-in list of ~500 high-volume US tickers.
"""

import csv
import io
import os
import time
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).parent / "data" / "universe_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_SECONDS = 24 * 60 * 60  # Refresh once a day

IWM_URL = (
    "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/"
    "1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
)
IWB_URL = (
    "https://www.ishares.com/us/products/239707/ishares-russell-1000-etf/"
    "1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
)


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.txt"


def _cache_fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS


def _fetch_ishares_csv(url: str) -> list[str]:
    """Fetch iShares ETF holdings CSV and extract tickers."""
    headers = {"User-Agent": "Mozilla/5.0 (research/educational use)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    text = resp.text

    # iShares CSV has header rows at the top — find the row starting with "Ticker"
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("Ticker,"):
            start = i
            break
    if start is None:
        return []

    reader = csv.DictReader(lines[start:])
    tickers = []
    for row in reader:
        t = (row.get("Ticker") or "").strip().strip('"')
        asset_class = (row.get("Asset Class") or "").strip().strip('"').lower()
        if t and t != "-" and "equity" in asset_class:
            # Clean up common format issues
            t = t.replace(".", "-")  # BRK.B -> BRK-B (yfinance format)
            tickers.append(t)
    return tickers


def load_russell_1000() -> list[str]:
    """Load Russell 1000 constituents (large-cap US)."""
    cache = _cache_path("russell_1000")
    if _cache_fresh(cache):
        return cache.read_text().strip().splitlines()

    try:
        tickers = _fetch_ishares_csv(IWB_URL)
        if tickers:
            cache.write_text("\n".join(tickers))
            print(f"[Universe] Loaded {len(tickers)} Russell 1000 stocks from iShares")
            return tickers
    except Exception as e:
        print(f"[Universe] iShares R1000 fetch failed: {e}")

    # Use stale cache if exists
    if cache.exists():
        return cache.read_text().strip().splitlines()

    return _FALLBACK_LARGE_CAPS


def load_russell_2000() -> list[str]:
    """Load Russell 2000 constituents (small/mid-cap US — spike candidates)."""
    cache = _cache_path("russell_2000")
    if _cache_fresh(cache):
        return cache.read_text().strip().splitlines()

    try:
        tickers = _fetch_ishares_csv(IWM_URL)
        if tickers:
            cache.write_text("\n".join(tickers))
            print(f"[Universe] Loaded {len(tickers)} Russell 2000 stocks from iShares")
            return tickers
    except Exception as e:
        print(f"[Universe] iShares R2000 fetch failed: {e}")

    if cache.exists():
        return cache.read_text().strip().splitlines()

    return _FALLBACK_SMALL_CAPS


def load_full_universe() -> list[str]:
    """Russell 1000 + Russell 2000 = ~3000 US stocks. Deduplicated."""
    r1000 = load_russell_1000()
    r2000 = load_russell_2000()
    combined = list(dict.fromkeys(r1000 + r2000))  # Preserve order, dedup
    print(f"[Universe] Total universe: {len(combined)} US stocks")
    return combined


# ─── Fallback lists (used if iShares fetch fails) ────────────────────

_FALLBACK_LARGE_CAPS = [
    # Mega caps
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "PG", "MA", "HD", "CVX", "ABBV",
    "MRK", "LLY", "AVGO", "PEP", "KO", "COST", "ORCL", "CRM", "BAC", "TMO",
    "MCD", "CSCO", "ACN", "ABT", "ADBE", "NKE", "DIS", "TXN", "CMCSA", "WFC",
    "VZ", "PM", "NEE", "RTX", "LIN", "DHR", "HON", "T", "AMD", "UPS", "QCOM",
    "INTC", "LOW", "IBM", "GS", "INTU", "CAT", "ISRG", "AMGN", "SBUX", "NOW",
    # Tech/growth
    "NFLX", "PYPL", "BKNG", "TMUS", "GE", "BLK", "SPGI", "DE", "AXP", "ELV",
    "PLD", "GILD", "SYK", "ADI", "LMT", "MDT", "TJX", "VRTX", "MMC", "SCHW",
    "PANW", "REGN", "CB", "SNPS", "ZTS", "CI", "MO", "BSX", "BMY", "ETN",
    "PGR", "SO", "DUK", "FI", "AON", "ITW", "CME", "EOG", "EQIX", "ICE",
]

_FALLBACK_SMALL_CAPS = [
    # Known high-volatility / meme / short-squeeze candidates
    "OPEN", "BIRD", "BBAI", "SOFI", "RIVN", "LCID", "HOOD", "PLTR", "DKNG",
    "COIN", "UPST", "AFRM", "CHWY", "ROKU", "UBER", "LYFT", "SNAP", "SHOP",
    "SQ", "TWLO", "DOCU", "ZM", "NET", "CRWD", "DDOG", "MDB", "OKTA", "SNOW",
    "BYND", "PTON", "CVNA", "FSLR", "ENPH", "PLUG", "NIO", "XPEV", "LI",
    "RKLB", "ACHR", "JOBY", "BLNK", "CHPT", "EVGO", "RUN", "SPWR",
    "GME", "AMC", "BBBY", "KOSS", "EXPR", "NAKD", "SNDL", "CLOV", "WISH",
    "MRIN", "MARA", "RIOT", "HUT", "BTBT", "BITF", "CLSK", "CORZ",
    "GRAB", "DIDI", "BEKE", "FUTU", "TIGR", "BILI", "NTES", "JD", "PDD",
    "MTN", "DASH", "ABNB", "CART", "INSP", "TDOC", "PACB", "CRSP", "EDIT",
    "NTLA", "BEAM", "VERV", "DNA", "RVMD", "VKTX", "SMMT", "TMDX",
    # Low-float movers historically
    "HKD", "AMTD", "MMAT", "BBIG", "ATER", "PROG", "GOEV", "MULN", "NILE",
    "IMPP", "TOP", "GNS", "BKKT", "HYSR", "BBLG", "AVCT", "WAVD", "BIAF",
]
