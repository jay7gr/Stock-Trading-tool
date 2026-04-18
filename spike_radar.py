"""
Spike Radar — scans the full US equity universe (Russell 1000 + 2000)
for stocks exhibiting early-stage spike behaviour.

Designed to catch moves like $OPEN, $BIRD, $BBAI in the first 30-90 mins.
Uses only free APIs: yfinance (batch quotes), Finnhub (news), StockTwits
(sentiment), Reddit (mentions).

Scoring model:
    spike_score = weighted sum of:
      - volume_ratio (today / 20-day avg)
      - |price_change %|
      - short_interest %
      - inverse float size (low float bonus)
      - news catalyst presence
      - social velocity (mention rate-of-change)
      - gap from prior close

Output: ranked list of spike candidates with actionable reasoning.
"""

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import config
from russell_universe import load_full_universe


@dataclass
class SpikeCandidate:
    ticker: str
    price: float
    prev_close: float
    pct_change: float
    volume: int
    volume_ratio: float
    market_cap: Optional[float] = None
    float_millions: Optional[float] = None
    short_interest_pct: Optional[float] = None
    gap_pct: float = 0.0
    news_count_24h: int = 0
    news_headlines: list = field(default_factory=list)
    social_mentions_delta: float = 0.0
    spike_score: float = 0.0
    action: str = "WATCH"  # WATCH, BUY, HOT, FADE
    reasoning: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": round(self.price, 2),
            "pct_change": round(self.pct_change, 2),
            "volume_ratio": round(self.volume_ratio, 2),
            "spike_score": round(self.spike_score, 1),
            "action": self.action,
            "float_M": round(self.float_millions, 1) if self.float_millions else None,
            "short_interest": round(self.short_interest_pct, 1) if self.short_interest_pct else None,
            "news_count": self.news_count_24h,
            "reasoning": " | ".join(self.reasoning),
        }


def _score_component(value: float, low: float, high: float) -> float:
    """Normalise a value into 0-100 range between low and high thresholds."""
    if value is None or math.isnan(value):
        return 0.0
    if value <= low:
        return 0.0
    if value >= high:
        return 100.0
    return ((value - low) / (high - low)) * 100.0


def compute_spike_score(c: SpikeCandidate) -> tuple[float, list[str]]:
    """Compute composite spike score from 0-100 with human-readable reasoning."""
    w = config.SPIKE_WEIGHTS
    reasons = []

    # 1. Volume ratio (2x = 40pts, 5x = 80pts, 10x+ = 100pts)
    vol_score = _score_component(c.volume_ratio, 1.0, 10.0)
    if c.volume_ratio >= 5:
        reasons.append(f"🔥 Volume {c.volume_ratio:.1f}x avg (explosive)")
    elif c.volume_ratio >= 2:
        reasons.append(f"↑ Volume {c.volume_ratio:.1f}x avg")

    # 2. Price change (5% = 25pts, 20% = 100pts)
    price_score = _score_component(abs(c.pct_change), 3.0, 20.0)
    if abs(c.pct_change) >= 10:
        reasons.append(f"⚡ Price {c.pct_change:+.1f}% today")
    elif abs(c.pct_change) >= 5:
        reasons.append(f"Price {c.pct_change:+.1f}%")

    # 3. Short interest (10% = 33pts, 30%+ = 100pts — squeeze setup)
    si_score = _score_component(c.short_interest_pct or 0, 5.0, 30.0)
    if (c.short_interest_pct or 0) >= 20:
        reasons.append(f"🎯 SI {c.short_interest_pct:.1f}% (squeeze fuel)")

    # 4. Low float bonus — <100M float = big bonus
    float_score = 0.0
    if c.float_millions:
        if c.float_millions < 20:
            float_score = 100.0
            reasons.append(f"💣 Float only {c.float_millions:.0f}M (ultra-low)")
        elif c.float_millions < 50:
            float_score = 80.0
            reasons.append(f"Float {c.float_millions:.0f}M (low)")
        elif c.float_millions < 100:
            float_score = 60.0
        elif c.float_millions < 300:
            float_score = 30.0

    # 5. News catalyst
    news_score = min((c.news_count_24h or 0) * 20.0, 100.0)
    if c.news_count_24h >= 3:
        reasons.append(f"📰 {c.news_count_24h} news items (24h)")

    # 6. Social mention velocity
    social_score = min(max(c.social_mentions_delta, 0) * 10.0, 100.0)
    if c.social_mentions_delta >= 5:
        reasons.append(f"📢 Social mentions +{c.social_mentions_delta:.0f}")

    # 7. Gap (5% gap = 50pts, 15% gap = 100pts)
    gap_score = _score_component(abs(c.gap_pct), 2.0, 15.0)
    if abs(c.gap_pct) >= 5:
        reasons.append(f"⚡ Gap {c.gap_pct:+.1f}%")

    # Weighted composite
    score = (
        w["volume_ratio"]    * vol_score +
        w["price_change"]    * price_score +
        w["short_interest"]  * si_score +
        w["low_float"]       * float_score +
        w["news_catalyst"]   * news_score +
        w["social_velocity"] * social_score +
        w["gap"]             * gap_score
    )

    return round(score, 1), reasons


def classify_action(c: SpikeCandidate) -> str:
    """Categorise based on score + direction + already-moved."""
    if c.spike_score < 40:
        return "WATCH"

    # Already moved too much → fade / avoid chasing
    if c.pct_change > 30:
        return "FADE"

    # Strong early-stage spike
    if c.spike_score >= 70 and 3 <= c.pct_change <= 15 and c.volume_ratio >= 3:
        return "HOT"

    # Good setup but not explosive yet
    if c.spike_score >= 55 and c.pct_change > 0:
        return "BUY"

    # Downside spike (high vol + falling) = potential short / avoid long
    if c.pct_change < -5 and c.volume_ratio >= 2:
        return "AVOID"

    return "WATCH"


# ─── Batch Quote Fetching ────────────────────────────────────────────

def fetch_batch_quotes(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch current price + volume for a batch of tickers via yfinance.
    Returns dict: ticker -> {price, prev_close, volume, avg_volume}.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("[SpikeRadar] yfinance not installed")
        return {}

    results = {}
    batch_size = config.YFINANCE_BATCH_SIZE

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        symbols = " ".join(batch)
        try:
            # 5-day window gives us prev close + 20-day avg via separate calc
            data = yf.download(
                symbols, period="5d", interval="1d",
                group_by="ticker", progress=False, threads=True,
                auto_adjust=True,
            )
            for ticker in batch:
                try:
                    if len(batch) == 1:
                        df = data
                    else:
                        df = data[ticker] if ticker in data.columns.get_level_values(0) else None
                    if df is None or df.empty or len(df) < 2:
                        continue
                    latest = df.iloc[-1]
                    prev = df.iloc[-2]
                    price = float(latest["Close"])
                    if math.isnan(price) or price <= 0:
                        continue
                    volume = int(latest["Volume"]) if not math.isnan(latest["Volume"]) else 0
                    avg_volume = int(df["Volume"].mean()) if len(df) >= 3 else volume
                    results[ticker] = {
                        "price": price,
                        "prev_close": float(prev["Close"]),
                        "volume": volume,
                        "avg_volume": max(avg_volume, 1),
                        "open_price": float(latest["Open"]),
                    }
                except Exception:
                    continue
            print(f"[SpikeRadar] Fetched batch {i//batch_size + 1}: "
                  f"{len(results)}/{i + len(batch)} tickers so far")
        except Exception as e:
            print(f"[SpikeRadar] Batch {i//batch_size} failed: {e}")
            continue

    return results


def fetch_fundamentals(ticker: str) -> dict:
    """Get float, short interest, market cap via yfinance (free, slow per-ticker)."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return {
            "float_millions": (info.get("floatShares") or 0) / 1_000_000 or None,
            "short_interest_pct": info.get("shortPercentOfFloat", 0) * 100 if info.get("shortPercentOfFloat") else None,
            "market_cap": info.get("marketCap"),
        }
    except Exception:
        return {}


# ─── Main Scanner ────────────────────────────────────────────────────

def scan_for_spikes(universe: Optional[list[str]] = None,
                    min_price: float = None,
                    max_price: float = None,
                    use_grok: bool = True) -> list[SpikeCandidate]:
    """
    Full pipeline: scan universe → filter movers → deep analysis → rank.
    Returns list of SpikeCandidate ordered by spike_score descending.

    If use_grok=True and GROK_API_KEY is set, the top candidates are
    enriched with Grok Live Search X/web sentiment verdicts.
    """
    min_price = min_price if min_price is not None else config.SPIKE_MIN_PRICE_USD
    max_price = max_price if max_price is not None else config.SPIKE_MAX_PRICE_USD

    if universe is None:
        universe = load_full_universe()

    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
          f"Spike Radar scanning {len(universe)} stocks")
    print(f"{'='*60}")

    # Step 1: Batch-fetch quotes for the full universe
    print(f"\n[1/4] Batch-fetching quotes ({len(universe)} tickers)...")
    quotes = fetch_batch_quotes(universe)
    print(f"      Got {len(quotes)} valid quotes")

    # Step 2: Initial filter — unusual movers only
    print(f"\n[2/4] Filtering for unusual movers...")
    candidates = []
    for ticker, q in quotes.items():
        price = q["price"]
        if price < min_price or price > max_price:
            continue
        prev = q["prev_close"]
        if prev <= 0:
            continue
        pct_change = (price - prev) / prev * 100
        vol_ratio = q["volume"] / max(q["avg_volume"], 1)

        # Must be moving AND on unusual volume
        if abs(pct_change) < config.SPIKE_MIN_PRICE_CHANGE_PCT:
            continue
        if vol_ratio < config.SPIKE_MIN_VOLUME_RATIO:
            continue

        gap_pct = (q["open_price"] - prev) / prev * 100 if prev > 0 else 0

        candidates.append(SpikeCandidate(
            ticker=ticker, price=price, prev_close=prev,
            pct_change=pct_change, volume=q["volume"],
            volume_ratio=vol_ratio, gap_pct=gap_pct,
        ))

    candidates.sort(key=lambda c: abs(c.pct_change) * c.volume_ratio, reverse=True)
    print(f"      {len(candidates)} stocks passed filter (>{config.SPIKE_MIN_PRICE_CHANGE_PCT}%, "
          f">{config.SPIKE_MIN_VOLUME_RATIO}x vol)")

    # Step 3: Deep enrichment on top shortlist (fundamentals + news)
    shortlist = candidates[:config.SPIKE_SHORTLIST_SIZE]
    print(f"\n[3/4] Deep analysis on top {len(shortlist)} candidates...")

    # Lazy imports — only load if modules exist
    try:
        from news_feed import get_recent_news_count
    except ImportError:
        get_recent_news_count = None
    try:
        from social_sentiment import get_mention_velocity
    except ImportError:
        get_mention_velocity = None

    for c in shortlist:
        fund = fetch_fundamentals(c.ticker)
        c.float_millions = fund.get("float_millions")
        c.short_interest_pct = fund.get("short_interest_pct")
        c.market_cap = fund.get("market_cap")

        if get_recent_news_count:
            try:
                news = get_recent_news_count(c.ticker)
                c.news_count_24h = news["count"]
                c.news_headlines = news["headlines"][:3]
            except Exception as e:
                print(f"  [{c.ticker}] news fetch failed: {e}")

        if get_mention_velocity:
            try:
                c.social_mentions_delta = get_mention_velocity(c.ticker)
            except Exception:
                pass

    # Step 4: Score and classify
    print(f"\n[4/4] Computing spike scores...")
    for c in shortlist:
        c.spike_score, c.reasoning = compute_spike_score(c)
        c.action = classify_action(c)

    shortlist.sort(key=lambda c: c.spike_score, reverse=True)

    # Optional Grok enrichment layer — live X/web validation
    if use_grok and config.GROK_API_KEY:
        print(f"\n[4b/4] Grok validation on top 10 candidates...")
        try:
            from grok_analyst import enrich_spike_candidates
            shortlist = enrich_spike_candidates(shortlist, top_n=10)
        except Exception as e:
            print(f"[Grok] Enrichment failed: {e}")

    # Log top results
    print(f"\n{'─'*60}")
    print(f"TOP SPIKE CANDIDATES")
    print(f"{'─'*60}")
    for i, c in enumerate(shortlist[:15], 1):
        print(f"{i:2}. {c.ticker:6} {c.action:5} score={c.spike_score:5.1f}  "
              f"{c.pct_change:+6.1f}%  vol={c.volume_ratio:4.1f}x  "
              f"{' | '.join(c.reasoning[:3])}")

    return shortlist


def alerts_only(candidates: list[SpikeCandidate]) -> list[SpikeCandidate]:
    """Filter for only the highest-conviction alerts."""
    return [c for c in candidates
            if c.spike_score >= config.SPIKE_ALERT_THRESHOLD
            and c.action in ("HOT", "BUY")]
