"""
News feed aggregator.

Sources (all free):
  - Finnhub: /company-news endpoint (60 req/min free)
  - yfinance: Ticker.news property (unlimited)
  - SEC EDGAR: 8-K / Form 4 filings (unlimited, public)

Unified output: list of dicts with {title, url, source, timestamp, relevance}.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

import config


# ─── Finnhub ─────────────────────────────────────────────────────────

def finnhub_news(ticker: str, days_back: int = 2) -> list[dict]:
    """Fetch company news from Finnhub (requires free API key)."""
    if not config.FINNHUB_API_KEY:
        return []
    end = datetime.now().date()
    start = end - timedelta(days=days_back)
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": ticker,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "token": config.FINNHUB_API_KEY,
            },
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json()
        return [
            {
                "title": item.get("headline", ""),
                "url": item.get("url", ""),
                "source": f"Finnhub/{item.get('source', 'unknown')}",
                "timestamp": datetime.fromtimestamp(item.get("datetime", 0), tz=timezone.utc).isoformat(),
                "summary": item.get("summary", ""),
                "relevance": 1.0,
            }
            for item in items if item.get("headline")
        ]
    except Exception as e:
        print(f"[Finnhub] {ticker} news error: {e}")
        return []


# ─── yfinance ────────────────────────────────────────────────────────

def yfinance_news(ticker: str) -> list[dict]:
    """Fetch news via yfinance (no key needed, unlimited)."""
    try:
        import yfinance as yf
        news = yf.Ticker(ticker).news or []
        results = []
        for item in news:
            content = item.get("content", item)
            title = content.get("title") or item.get("title", "")
            if not title:
                continue
            pub_time = content.get("pubDate") or item.get("providerPublishTime")
            if isinstance(pub_time, (int, float)):
                ts = datetime.fromtimestamp(pub_time, tz=timezone.utc).isoformat()
            elif isinstance(pub_time, str):
                ts = pub_time
            else:
                ts = datetime.now(timezone.utc).isoformat()
            results.append({
                "title": title,
                "url": content.get("canonicalUrl", {}).get("url") or item.get("link", ""),
                "source": f"Yahoo/{content.get('provider', {}).get('displayName', 'unknown')}",
                "timestamp": ts,
                "summary": content.get("summary", ""),
                "relevance": 1.0,
            })
        return results
    except Exception as e:
        print(f"[yfinance] {ticker} news error: {e}")
        return []


# ─── SEC EDGAR ───────────────────────────────────────────────────────

def sec_recent_filings(ticker: str, days_back: int = 2) -> list[dict]:
    """
    Fetch recent 8-K (material events) and Form 4 (insider trades) filings.
    SEC EDGAR is free and unlimited but rate-limited to 10 req/sec.
    """
    try:
        headers = {"User-Agent": "StockTradingTool research@example.com"}
        # First resolve ticker → CIK
        resp = requests.get(
            "https://www.sec.gov/cgi-bin/browse-edgar",
            params={
                "action": "getcompany",
                "CIK": ticker,
                "type": "8-K",
                "dateb": "",
                "owner": "include",
                "count": "10",
                "output": "atom",
            },
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        # Very light parse — look for <title> entries
        text = resp.text
        filings = []
        # Split into entries
        for entry in text.split("<entry>")[1:]:
            title_start = entry.find("<title>") + len("<title>")
            title_end = entry.find("</title>")
            updated_start = entry.find("<updated>") + len("<updated>")
            updated_end = entry.find("</updated>")
            link_start = entry.find('<link rel="alternate"')
            href_start = entry.find('href="', link_start) + 6
            href_end = entry.find('"', href_start)

            if title_start > 0 and title_end > title_start:
                title = entry[title_start:title_end]
                ts = entry[updated_start:updated_end] if updated_start > 0 else ""
                url = entry[href_start:href_end] if href_start > 0 else ""
                if "8-K" in title or "Form 4" in title:
                    filings.append({
                        "title": f"SEC: {title}",
                        "url": url,
                        "source": "SEC EDGAR",
                        "timestamp": ts,
                        "summary": "",
                        "relevance": 1.5,  # SEC filings often material
                    })
        return filings[:5]
    except Exception as e:
        print(f"[SEC] {ticker} filings error: {e}")
        return []


# ─── Unified Interface ───────────────────────────────────────────────

def get_all_news(ticker: str, days_back: int = 2) -> list[dict]:
    """Aggregate news from all sources, sorted by timestamp descending."""
    all_news = []
    all_news.extend(finnhub_news(ticker, days_back))
    all_news.extend(yfinance_news(ticker))
    # SEC is slower, only fetch for shortlist
    # all_news.extend(sec_recent_filings(ticker, days_back))

    # Dedupe by title
    seen_titles = set()
    deduped = []
    for item in all_news:
        key = item["title"][:80].lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        deduped.append(item)

    deduped.sort(key=lambda x: x["timestamp"], reverse=True)
    return deduped


def get_recent_news_count(ticker: str, hours: int = 24) -> dict:
    """Fast summary: how many news items in last N hours, plus top 3 headlines."""
    news = get_all_news(ticker, days_back=max(1, hours // 24 + 1))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    recent = []
    for item in news:
        try:
            ts_str = item["timestamp"].replace("Z", "+00:00")
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                recent.append(item)
        except Exception:
            continue

    return {
        "count": len(recent),
        "headlines": [item["title"] for item in recent[:5]],
        "items": recent[:10],
    }


def has_material_catalyst(ticker: str) -> tuple[bool, str]:
    """
    Quick check: is there a material catalyst in the last 24h?
    Returns (True/False, reason).
    """
    summary = get_recent_news_count(ticker, hours=24)
    if summary["count"] == 0:
        return False, "No news"

    keywords = [
        "earnings", "beats", "misses", "upgrade", "downgrade", "price target",
        "acquisition", "merger", "buyout", "fda", "approval", "guidance",
        "partnership", "contract", "lawsuit", "investigation", "restructuring",
        "dividend", "buyback", "split", "spin-off", "short squeeze",
        "insider buy", "13d", "activist", "hedge fund",
    ]
    for item in summary["items"]:
        title_lower = item["title"].lower()
        for kw in keywords:
            if kw in title_lower:
                return True, f"{kw.upper()} — {item['title']}"

    return summary["count"] >= 3, f"{summary['count']} news items in 24h"
