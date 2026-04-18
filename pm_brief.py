"""
Portfolio Manager Daily Brief.

Each morning, generate a discretionary-PM-style brief covering:
  1. Overnight market context (indices, VIX, sector ETFs)
  2. Portfolio health: positions with P&L, news on holdings
  3. Earnings calendar (today + tomorrow)
  4. Spike radar: top 10 candidates ranked
  5. Trending social tickers to watch

Output: structured markdown + JSON (for dashboard / Telegram).
"""

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import config
from spike_radar import scan_for_spikes, SpikeCandidate
from news_feed import get_all_news, has_material_catalyst
from social_sentiment import trending_tickers_now


MACRO_TICKERS = {
    "SPY": "S&P 500",
    "QQQ": "NASDAQ 100",
    "IWM": "Russell 2000",
    "DIA": "Dow Jones",
    "VIX": "Volatility Index",
    "XLK": "Tech",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLY": "Consumer Disc.",
    "GLD": "Gold",
    "TLT": "20Y Treasuries",
}


@dataclass
class BriefSection:
    title: str
    content: str
    data: dict = field(default_factory=dict)


@dataclass
class DailyBrief:
    timestamp: str
    date: str
    sections: list = field(default_factory=list)

    def add_section(self, title: str, content: str, data: Optional[dict] = None):
        self.sections.append(BriefSection(title, content, data or {}))

    def to_markdown(self) -> str:
        lines = [
            f"# 📊 PM Daily Brief — {self.date}",
            f"_Generated {self.timestamp} UTC_",
            "",
        ]
        for section in self.sections:
            lines.append(f"## {section.title}")
            lines.append(section.content)
            lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "date": self.date,
            "sections": [asdict(s) for s in self.sections],
        }


# ─── Section Generators ──────────────────────────────────────────────

def macro_snapshot() -> BriefSection:
    """Pre-market / overnight market context."""
    try:
        import yfinance as yf
        tickers = list(MACRO_TICKERS.keys())
        data = yf.download(" ".join(tickers), period="5d", progress=False,
                          group_by="ticker", auto_adjust=True, threads=True)

        lines = ["| Instrument | Price | Δ Today | Δ 5d |",
                 "|---|---:|---:|---:|"]
        snapshot = {}
        for t in tickers:
            try:
                df = data[t] if t in data.columns.get_level_values(0) else data
                if len(df) < 2:
                    continue
                close = float(df["Close"].iloc[-1])
                prev = float(df["Close"].iloc[-2])
                week = float(df["Close"].iloc[0])
                day_chg = (close - prev) / prev * 100
                week_chg = (close - week) / week * 100
                lines.append(
                    f"| {MACRO_TICKERS[t]} ({t}) | {close:.2f} | "
                    f"{day_chg:+.2f}% | {week_chg:+.2f}% |"
                )
                snapshot[t] = {"price": close, "day_chg": day_chg, "week_chg": week_chg}
            except Exception:
                continue

        return BriefSection(
            title="🌍 Market Snapshot",
            content="\n".join(lines),
            data=snapshot,
        )
    except Exception as e:
        return BriefSection(
            title="🌍 Market Snapshot",
            content=f"_Could not fetch market data: {e}_",
        )


def portfolio_health(portfolio_positions: dict) -> BriefSection:
    """Review each current holding with news check."""
    if not portfolio_positions:
        return BriefSection(
            title="💼 Portfolio Health",
            content="_No open positions. Cash-only mode._",
        )

    lines = ["| Ticker | Price | P&L | Catalyst | Action |",
             "|---|---:|---:|---|---|"]
    detail = {}
    for ticker, pos in portfolio_positions.items():
        has_catalyst, catalyst_reason = has_material_catalyst(ticker)
        price = pos.get("current_price", 0)
        pnl_pct = pos.get("pnl_pct", 0)

        # Suggest action based on P&L + catalyst
        if pnl_pct >= 10 and has_catalyst:
            action = "🟢 TRIM (secure gains)"
        elif pnl_pct <= -5:
            action = "🔴 REVIEW STOP"
        elif has_catalyst and pnl_pct > 0:
            action = "🟡 HOLD + WATCH"
        else:
            action = "⚪ HOLD"

        catalyst_short = catalyst_reason[:50] + "…" if len(catalyst_reason) > 50 else catalyst_reason
        lines.append(
            f"| {ticker} | {price:.2f} | {pnl_pct:+.1f}% | "
            f"{catalyst_short} | {action} |"
        )
        detail[ticker] = {
            "price": price, "pnl_pct": pnl_pct,
            "catalyst": catalyst_reason, "action": action,
        }

    return BriefSection(
        title="💼 Portfolio Health",
        content="\n".join(lines),
        data=detail,
    )


def earnings_calendar() -> BriefSection:
    """Stocks reporting earnings today/tomorrow."""
    if not config.FINNHUB_API_KEY:
        return BriefSection(
            title="📅 Earnings Today",
            content="_Requires FINNHUB_API_KEY for earnings calendar._",
        )

    try:
        import requests
        from datetime import date, timedelta
        today = date.today()
        tomorrow = today + timedelta(days=1)
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={
                "from": today.isoformat(),
                "to": tomorrow.isoformat(),
                "token": config.FINNHUB_API_KEY,
            },
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("earningsCalendar", [])[:15]

        if not items:
            return BriefSection(
                title="📅 Earnings Today",
                content="_No earnings flagged._",
            )

        lines = ["| Symbol | Date | Time | EPS Est | Rev Est |",
                 "|---|---|---|---:|---:|"]
        for item in items:
            lines.append(
                f"| {item.get('symbol', '?')} | {item.get('date', '?')} | "
                f"{item.get('hour', '?')} | "
                f"{item.get('epsEstimate', 'n/a')} | "
                f"{item.get('revenueEstimate', 'n/a')} |"
            )

        return BriefSection(
            title="📅 Earnings Today / Tomorrow",
            content="\n".join(lines),
            data={"items": items},
        )
    except Exception as e:
        return BriefSection(
            title="📅 Earnings Today",
            content=f"_Error fetching earnings: {e}_",
        )


def spike_candidates_section(max_items: int = 10) -> BriefSection:
    """Top spike candidates from the radar."""
    try:
        candidates = scan_for_spikes()
    except Exception as e:
        return BriefSection(
            title="🚨 Spike Radar",
            content=f"_Scan failed: {e}_",
        )

    if not candidates:
        return BriefSection(
            title="🚨 Spike Radar",
            content="_No candidates passed filters today._",
        )

    top = candidates[:max_items]
    lines = [
        "| Rank | Ticker | Score | Action | Today % | Vol | Float | SI | Reasoning |",
        "|---:|---|---:|---|---:|---:|---:|---:|---|",
    ]
    data = []
    for i, c in enumerate(top, 1):
        float_str = f"{c.float_millions:.0f}M" if c.float_millions else "?"
        si_str = f"{c.short_interest_pct:.1f}%" if c.short_interest_pct else "?"
        reason = " · ".join(c.reasoning[:3])
        lines.append(
            f"| {i} | **{c.ticker}** | {c.spike_score:.0f} | {c.action} | "
            f"{c.pct_change:+.1f}% | {c.volume_ratio:.1f}x | "
            f"{float_str} | {si_str} | {reason} |"
        )
        data.append(c.to_dict())

    return BriefSection(
        title="🚨 Spike Radar — Top Candidates",
        content="\n".join(lines),
        data={"candidates": data},
    )


def social_trending_section() -> BriefSection:
    """What's hot on StockTwits + Reddit right now."""
    try:
        trending = trending_tickers_now(top_n=15)
    except Exception as e:
        return BriefSection(
            title="📢 Social Buzz",
            content=f"_Error: {e}_",
        )

    if not trending:
        return BriefSection(
            title="📢 Social Buzz",
            content="_No trending tickers detected._",
        )

    lines = ["| Ticker | Mention Score |", "|---|---:|"]
    for ticker, count in trending:
        lines.append(f"| {ticker} | {count} |")

    return BriefSection(
        title="📢 Social Buzz (Reddit + StockTwits)",
        content="\n".join(lines),
        data={"trending": [{"ticker": t, "count": c} for t, c in trending]},
    )


# ─── Main Brief Generator ────────────────────────────────────────────

def generate_brief(portfolio_positions: Optional[dict] = None,
                   include_spike_scan: bool = True) -> DailyBrief:
    """
    Generate a complete PM daily brief.
    Pass portfolio_positions as dict of ticker -> {current_price, pnl_pct, ...}.
    """
    now = datetime.utcnow()
    brief = DailyBrief(
        timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
        date=now.strftime("%A, %d %B %Y"),
    )

    brief.add_section(
        title="📝 TL;DR",
        content="_Summary will be populated after all sections run._",
    )

    # Macro snapshot (fast)
    print("[Brief] Fetching macro snapshot...")
    brief.sections.append(macro_snapshot())

    # Portfolio
    print("[Brief] Reviewing portfolio...")
    brief.sections.append(portfolio_health(portfolio_positions or {}))

    # Earnings
    print("[Brief] Fetching earnings calendar...")
    brief.sections.append(earnings_calendar())

    # Spike radar (slow — full universe scan)
    if include_spike_scan:
        print("[Brief] Running spike radar (this takes a few minutes)...")
        brief.sections.append(spike_candidates_section(max_items=config.MAX_IDEAS_PER_BRIEF))

    # Social
    print("[Brief] Fetching social trends...")
    brief.sections.append(social_trending_section())

    return brief


def save_brief(brief: DailyBrief, output_dir: str = "data/briefs") -> tuple[Path, Path]:
    """Save brief as both markdown and JSON. Returns paths."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    md_path = out_dir / f"brief_{stamp}.md"
    md_path.write_text(brief.to_markdown())

    json_path = out_dir / f"brief_{stamp}.json"
    json_path.write_text(json.dumps(brief.to_dict(), indent=2, default=str))

    # Also update "latest" symlinks for dashboard consumption
    latest_md = out_dir / "latest.md"
    latest_json = out_dir / "latest.json"
    latest_md.write_text(brief.to_markdown())
    latest_json.write_text(json.dumps(brief.to_dict(), indent=2, default=str))

    return md_path, json_path


if __name__ == "__main__":
    brief = generate_brief(include_spike_scan=True)
    md, js = save_brief(brief)
    print(f"\nBrief saved:\n  Markdown: {md}\n  JSON: {js}")
    print("\n" + "=" * 70)
    print(brief.to_markdown())
