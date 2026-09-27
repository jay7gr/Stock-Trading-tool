"""
Bar-based stop/target evaluation (replaces mark-only checks).

Rules (per 2026-09-25 postmortem):
  * Scan 1m bars from the later of floor_minute(opened_at) and last_check.
  * First bar with Low <= stop exits at min(stop, bar Open)  (gap-through fills at the Open).
  * First bar with High >= target exits at target.
  * If both hit in the same bar, the stop wins (conservative).
  * last_check = end of the last processed bar (bars with start >= last_check are new).
  * By default only completed bars are used (bar start + 1m <= now).
  * Bad-tick guard: a triggering bar whose trigger price (Low for stops,
    High for targets) is more than BAD_TICK_PCT (1.5%) away from the previous
    bar's Close needs confirmation by the NEXT bar (next Low <= stop / next
    High >= target). If the next bar does not confirm, the print is treated
    as a bad tick and skipped; if there is no next bar yet (live), the check
    holds and re-examines that bar on the next pass. Added after the
    2026-09-25 ISPY 14:28 print (3665p, 30 shares, ~2% below prints either
    side while USPY.L/CIBR.L were flat).
Prices: bars are in vendor units; `to_book` converts them to the book's units
before comparing against stop/target (e.g. GBX -> GBP).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Callable, Optional

BAD_TICK_PCT = 0.015

import pandas as pd

from quotes import to_london

BAR = timedelta(minutes=1)


@dataclass
class BarExit:
    kind: str                 # "stop" | "target"
    bar_ts: datetime          # bar start (London)
    price: float              # exit price in book units
    level: float              # the stop or target level
    gapped: bool
    bar_open: float
    bar_high: float
    bar_low: float
    bar_close: float
    skipped_bad_ticks: tuple = ()
    gbp_price: Optional[float] = None   # set by the emulator when levels are in a non-GBP currency

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bar_ts"] = self.bar_ts.isoformat()
        d["skipped_bad_ticks"] = [t.isoformat() if hasattr(t, "isoformat") else t
                                  for t in self.skipped_bad_ticks]
        return d


@dataclass
class CheckResult:
    exit: Optional[BarExit]
    last_check: Optional[datetime]   # new last_check (end of last processed bar)
    last_close: Optional[float]      # last close in book units (mark)
    last_bar_ts: Optional[datetime]
    bars_scanned: int
    skipped_bad_ticks: tuple = ()


def floor_minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


def evaluate_bars(bars: pd.DataFrame, stop: float, target: float,
                  to_book: Callable[[float], float] = lambda x: x,
                  prev_close: Optional[float] = None,
                  bad_tick_pct: float = BAD_TICK_PCT,
                  ) -> tuple[Optional[BarExit], int, Optional[datetime], list]:
    """Walk bars in order and return (first exit or None, bars scanned,
    hold_ts, skipped_bad_ticks).

    hold_ts is set when the latest bar is a suspicious trigger awaiting
    confirmation by the next bar; the caller must re-check from hold_ts.
    prev_close (book units) is the close of the bar before `bars[0]`.
    """
    rows = [(to_london(ts), *(to_book(float(r[k])) for k in ("Open", "High", "Low", "Close")))
            for ts, r in bars.iterrows()]
    skipped: list = []
    n = 0
    prev = prev_close
    for i, (ts, o, h, l, c) in enumerate(rows):
        n += 1
        stop_hit = stop is not None and stop > 0 and l <= stop
        tgt_hit = target is not None and target > 0 and h >= target
        if stop_hit or tgt_hit:
            kind = "stop" if stop_hit else "target"  # stop wins when both hit in one bar
            trig = l if stop_hit else h
            suspicious = bool(prev and bad_tick_pct and abs(trig / prev - 1) > bad_tick_pct)
            if suspicious:
                if i + 1 >= len(rows):
                    return None, n, ts, skipped          # wait for the next bar
                _, _, nh, nl, _ = rows[i + 1]
                confirmed = nl <= stop if kind == "stop" else nh >= target
                if not confirmed:
                    skipped.append(ts)
                    continue                            # bad tick: ignore; prev stays
            if kind == "stop":
                gapped = o <= stop
                return BarExit("stop", ts, min(stop, o) if gapped else stop, stop, gapped,
                               o, h, l, c, tuple(skipped)), n, None, skipped
            return BarExit("target", ts, target, target, o >= target, o, h, l, c,
                           tuple(skipped)), n, None, skipped
        prev = c
    return None, n, None, skipped


def check_position(bars: pd.DataFrame, *, stop: float, target: float,
                   opened_at, last_check=None, now: Optional[datetime] = None,
                   to_book: Callable[[float], float] = lambda x: x,
                   completed_only: bool = True,
                   bad_tick_pct: float = BAD_TICK_PCT) -> CheckResult:
    """Apply the bar rules to one position given raw bars (vendor units)."""
    opened = floor_minute(to_london(opened_at))
    since = opened
    lc = to_london(last_check) if last_check else None
    if lc and lc > since:
        since = lc
    if now is not None and len(bars):
        now = to_london(now)
        cutoff = now - BAR if completed_only else now
        bars = bars[bars.index <= cutoff]
    df = bars[bars.index >= since] if len(bars) else bars
    if not len(df):
        return CheckResult(None, lc, None, None, 0)
    before = bars[bars.index < since] if len(bars) else bars
    prev_close = to_book(float(before["Close"].iloc[-1])) if len(before) else None
    ex, n, hold_ts, skipped = evaluate_bars(df, stop, target, to_book, prev_close, bad_tick_pct)
    skipped = tuple(skipped)
    if ex:
        return CheckResult(ex, ex.bar_ts + BAR, ex.price, ex.bar_ts, n, skipped)
    last_ts = to_london(df.index[-1])
    mark = to_book(float(df["Close"].iloc[-1]))
    if hold_ts is not None:
        # suspicious trigger on the latest bar: re-check it next pass, and do not
        # mark to the unconfirmed print (use the prior bar's close if any)
        prior = bars[bars.index < hold_ts]
        if len(prior):
            mark = to_book(float(prior["Close"].iloc[-1]))
        return CheckResult(None, hold_ts, mark, last_ts, n, skipped)
    return CheckResult(None, last_ts + BAR, mark, last_ts, n, skipped)
