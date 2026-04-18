"""
Social sentiment tracker.

Free sources (no API key required):
  - StockTwits: trending symbols + per-symbol sentiment
  - Reddit JSON: r/wallstreetbets, r/stocks hot posts → ticker mentions

Key metric: mention VELOCITY (rate of change), not absolute count.
A stock going from 0 mentions to 20 is more interesting than one stuck at 50.
"""

import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

CACHE_DIR = Path(__file__).parent / "data" / "social_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ─── StockTwits ──────────────────────────────────────────────────────

STOCKTWITS_TRENDING_URL = "https://api.stocktwits.com/api/2/trending/symbols.json"
STOCKTWITS_SYMBOL_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"


def stocktwits_trending() -> list[dict]:
    """Fetch currently trending symbols from StockTwits."""
    try:
        resp = requests.get(
            STOCKTWITS_TRENDING_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "ticker": s.get("symbol"),
                "name": s.get("title", ""),
                "watchlist_count": s.get("watchlist_count", 0),
            }
            for s in data.get("symbols", [])
        ]
    except Exception as e:
        print(f"[StockTwits] trending error: {e}")
        return []


def stocktwits_sentiment(ticker: str) -> dict:
    """
    Get recent messages for a ticker. Each message has sentiment tag.
    Returns {message_count, bullish_pct, bearish_pct, recent_volume}.
    """
    try:
        resp = requests.get(
            STOCKTWITS_SYMBOL_URL.format(symbol=ticker),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.status_code != 200:
            return {"message_count": 0, "bullish_pct": 0, "bearish_pct": 0}
        data = resp.json()
        messages = data.get("messages", [])

        bullish = 0
        bearish = 0
        for msg in messages:
            sentiment = (msg.get("entities") or {}).get("sentiment") or {}
            basic = sentiment.get("basic") if isinstance(sentiment, dict) else None
            if basic == "Bullish":
                bullish += 1
            elif basic == "Bearish":
                bearish += 1

        total_tagged = bullish + bearish
        return {
            "message_count": len(messages),
            "bullish_pct": (bullish / total_tagged * 100) if total_tagged else 0,
            "bearish_pct": (bearish / total_tagged * 100) if total_tagged else 0,
            "recent_messages": [m.get("body", "")[:200] for m in messages[:5]],
        }
    except Exception as e:
        print(f"[StockTwits] {ticker} error: {e}")
        return {"message_count": 0, "bullish_pct": 0, "bearish_pct": 0}


# ─── Reddit ──────────────────────────────────────────────────────────

REDDIT_SUBS = ["wallstreetbets", "stocks", "investing", "pennystocks", "smallstreetbets"]
TICKER_RE = re.compile(r'\b\$?([A-Z]{2,5})\b')
# Words that match ticker regex but aren't tickers
TICKER_BLACKLIST = {
    "CEO", "CFO", "CTO", "IPO", "USA", "FDA", "SEC", "IRS", "GDP", "CPI",
    "ETF", "ETFS", "NYSE", "THE", "AND", "FOR", "YOLO", "DD", "WSB", "FOMO",
    "YOU", "ALL", "NEW", "OLD", "BIG", "BUY", "SELL", "HOLD", "CALL", "PUT",
    "WTF", "TLDR", "USD", "EUR", "GBP", "PM", "AM", "ET", "ATH", "ATL",
    "HOT", "SHIT", "HYPE", "GAIN", "LOSS", "LONG", "SHORT", "MOON", "BEAR",
    "BULL", "AH", "PT", "EOD", "EOW", "PNL", "ROFL", "OK", "NO", "YES",
    "GOOD", "BAD", "GM", "GN", "LMAO", "LOL", "HODL", "LOAD", "AI",
}


def reddit_hot_posts(subreddit: str, limit: int = 50) -> list[dict]:
    """Fetch hot posts from a subreddit via public JSON API."""
    try:
        resp = requests.get(
            f"https://www.reddit.com/r/{subreddit}/hot.json",
            params={"limit": limit},
            headers={"User-Agent": "StockTradingTool/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": c["data"]["title"],
                "selftext": c["data"].get("selftext", "")[:500],
                "score": c["data"]["score"],
                "num_comments": c["data"]["num_comments"],
                "created_utc": c["data"]["created_utc"],
                "url": c["data"]["url"],
            }
            for c in data["data"]["children"]
        ]
    except Exception as e:
        print(f"[Reddit] r/{subreddit} error: {e}")
        return []


def extract_tickers(text: str) -> list[str]:
    """Extract likely stock tickers from text (filters blacklist)."""
    candidates = TICKER_RE.findall(text.upper())
    return [t for t in candidates if t not in TICKER_BLACKLIST]


def reddit_ticker_mentions(hours: int = 6) -> Counter:
    """
    Count ticker mentions across top subreddits in recent hot posts.
    Returns Counter of ticker -> mention count.
    """
    cutoff = time.time() - hours * 3600
    mentions = Counter()

    for sub in REDDIT_SUBS:
        posts = reddit_hot_posts(sub, limit=50)
        for post in posts:
            if post["created_utc"] < cutoff:
                continue
            text = f"{post['title']} {post['selftext']}"
            tickers = extract_tickers(text)
            # Weight by engagement: upvotes + comments
            weight = 1 + post["score"] // 100 + post["num_comments"] // 10
            for t in set(tickers):  # Unique per post
                mentions[t] += weight

    return mentions


# ─── Mention Velocity (key metric) ───────────────────────────────────

def get_mention_velocity(ticker: str) -> float:
    """
    Compute mention velocity: (current 6h count) - (prior 6h count).
    Requires snapshots — uses a simple file cache.
    Returns delta (positive = trending up).
    """
    cache_file = CACHE_DIR / "mention_history.json"
    ticker = ticker.upper()

    # Load cache
    history = {}
    if cache_file.exists():
        try:
            history = json.loads(cache_file.read_text())
        except Exception:
            history = {}

    current_mentions = reddit_ticker_mentions(hours=6)
    current_count = current_mentions.get(ticker, 0)

    # Compare to snapshot taken >6h ago
    snapshots = history.get(ticker, [])
    snapshots.append({"ts": time.time(), "count": current_count})
    # Keep only last 24h of snapshots
    snapshots = [s for s in snapshots if time.time() - s["ts"] < 86400]
    history[ticker] = snapshots

    cache_file.write_text(json.dumps(history))

    # Find snapshot from 6h ago
    prior = None
    for s in snapshots:
        if 5 * 3600 < time.time() - s["ts"] < 12 * 3600:
            prior = s["count"]
            break

    if prior is None:
        return float(current_count)  # No baseline yet — use absolute count

    return float(current_count - prior)


def trending_tickers_now(top_n: int = 30) -> list[tuple[str, int]]:
    """Return list of (ticker, mention_count) for currently hot tickers."""
    mentions = reddit_ticker_mentions(hours=6)

    # Also pull StockTwits trending
    try:
        st_trending = stocktwits_trending()
        for item in st_trending:
            t = item.get("ticker")
            if t and t not in TICKER_BLACKLIST:
                mentions[t] += max(10, item.get("watchlist_count", 0) // 1000)
    except Exception:
        pass

    return mentions.most_common(top_n)
