"""
Quotes & intraday bars with explicit timestamps.

- fetch_quote(): last trade price + the exchange time of that trade
  (yfinance chart metadata regularMarketTime), falling back to the start
  time of the latest 1m bar (conservative: the real print is at or after it).
- get_fresh_quote(): enforces MAX_QUOTE_AGE_S (60s) at fill time; re-quotes
  once and raises StaleQuoteError if the quote is still stale.
- fetch_bars(): 1m OHLC bars (Europe/London index) with 429 backoff.

All yfinance calls go through _with_backoff() because Yahoo rate-limits (429).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

import config

LONDON = ZoneInfo("Europe/London")
MAX_QUOTE_AGE_S = float(getattr(config, "MAX_QUOTE_AGE_S", 60))
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "tests", "fixtures")


class StaleQuoteError(RuntimeError):
    """Quote older than MAX_QUOTE_AGE_S at fill time (after a re-quote)."""


@dataclass
class Quote:
    symbol: str          # vendor symbol (yfinance)
    price: float         # raw vendor units (GBX for GBp lines)
    ts: datetime         # tz-aware time of the quoted trade
    source: str

    def age_s(self, at: Optional[datetime] = None) -> float:
        at = at or now_london()
        return (to_london(at) - to_london(self.ts)).total_seconds()

    def is_fresh(self, at: Optional[datetime] = None,
                 max_age_s: float = MAX_QUOTE_AGE_S) -> bool:
        return self.age_s(at) <= max_age_s


def now_london() -> datetime:
    return datetime.now(LONDON)


def to_london(ts) -> datetime:
    """Parse/normalise a timestamp. Naive values are treated as London local."""
    if ts is None or ts == "":
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if ts.tzinfo is None:
        return ts.replace(tzinfo=LONDON)
    return ts.astimezone(LONDON)


def _is_rate_limit(exc: Exception) -> bool:
    s = f"{type(exc).__name__} {exc}"
    return "RateLimit" in s or "429" in s or "Too Many Requests" in s


def _with_backoff(fn: Callable, tries: int = 5, base_s: float = 4.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if not _is_rate_limit(e) and i >= 1:
                break
            time.sleep(base_s * (2 ** i))
    if last:
        raise last
    return None


def _normalise_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = df.copy()
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize(LONDON)
    df.index = idx.tz_convert(LONDON)
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    return df[cols].sort_index()


def fetch_bars(yf_symbol: str, start: datetime, end: Optional[datetime] = None) -> pd.DataFrame:
    """1m bars from `start` (inclusive) up to `end` (default: now)."""
    import yfinance as yf
    start = to_london(start)
    end = to_london(end) if end else now_london() + timedelta(minutes=1)
    # yfinance start/end are dates-or-datetimes; pad and filter locally.
    s = (start - timedelta(minutes=1)).astimezone(ZoneInfo("UTC"))
    e = (end + timedelta(minutes=1)).astimezone(ZoneInfo("UTC"))

    def _call():
        df = yf.Ticker(yf_symbol).history(start=s, end=e, interval="1m", prepost=False)
        return df
    df = _normalise_bars(_with_backoff(_call))
    return df[(df.index >= start.replace(second=0, microsecond=0)) & (df.index <= end)]


def fixture_path(yf_symbol: str, day: date | str) -> str:
    return os.path.join(FIXTURE_DIR, f"{yf_symbol}_1m_{day}.csv")


def load_day_bars(yf_symbol: str, day: date | str, allow_fetch: bool = True,
                  save: bool = True) -> pd.DataFrame:
    """Bars for a whole day: cached fixture first, else yfinance (saved to fixtures)."""
    path = fixture_path(yf_symbol, day)
    if os.path.exists(path):
        return _normalise_bars(pd.read_csv(path, index_col=0, parse_dates=True))
    if not allow_fetch:
        raise FileNotFoundError(path)
    d = date.fromisoformat(str(day))
    start = datetime(d.year, d.month, d.day, 0, 0, tzinfo=LONDON)
    df = fetch_bars(yf_symbol, start, start + timedelta(days=1))
    if save and not df.empty:
        os.makedirs(FIXTURE_DIR, exist_ok=True)
        df.to_csv(path)
    return df


def fetch_quote(yf_symbol: str) -> Optional[Quote]:
    """Last trade + its exchange timestamp."""
    import yfinance as yf

    def _call():
        t = yf.Ticker(yf_symbol)
        df = t.history(period="1d", interval="1m", prepost=False)
        meta = {}
        try:
            meta = t.history_metadata or {}
        except Exception:  # noqa: BLE001
            meta = {}
        return df, meta
    df, meta = _with_backoff(_call)
    px, ts = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if px and ts is not None:
        if not isinstance(ts, (datetime, pd.Timestamp)):
            ts = datetime.fromtimestamp(int(ts), tz=ZoneInfo("UTC"))
        return Quote(yf_symbol, float(px), to_london(ts), "yfinance_chart_regularMarketTime")
    df = _normalise_bars(df)
    if df.empty:
        return None
    return Quote(yf_symbol, float(df["Close"].iloc[-1]), df.index[-1].to_pydatetime(),
                 "yfinance_1m_bar_start")


def get_fresh_quote(yf_symbol: str, at: Optional[datetime] = None,
                    max_age_s: float = MAX_QUOTE_AGE_S,
                    fetch: Callable[[str], Optional[Quote]] = fetch_quote,
                    requote_wait_s: float = 5.0,
                    existing: Optional[Quote] = None) -> Quote:
    """Return a quote no older than max_age_s at `at` (default now).

    Uses `existing` if already fresh; otherwise re-quotes (up to 2 tries).
    Raises StaleQuoteError if nothing fresh is available.
    """
    if existing is not None and existing.is_fresh(at, max_age_s):
        return existing
    q = None
    for attempt in range(2):
        q = fetch(yf_symbol)
        check_at = at or now_london()
        if q is not None and q.is_fresh(check_at, max_age_s):
            return q
        if attempt == 0 and requote_wait_s:
            time.sleep(requote_wait_s)
    age = f"{q.age_s(at):.0f}s old (ts {q.ts.isoformat()})" if q else "no quote"
    raise StaleQuoteError(f"{yf_symbol}: quote stale after re-quote: {age}; max {max_age_s:.0f}s")
