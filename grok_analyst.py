"""
Grok Analyst Layer — real-time X (Twitter) + web intelligence via xAI API.

Unique value: The only layer with live X access. Detects narrative catalysts
the quant radar can't see (hedge fund mentions, CEO tweets, viral threads).

Inspired by $OPEN (Eric Jackson's tweet) and $BIRD (short squeeze narrative) —
moves that the quant radar alone would flag late.

Three integration points:
  1. validate_spike(): confirms WHY a stock is moving via X search
  2. check_position(): daily sentiment drift check on holdings
  3. write_narrative(): PM-style TL;DR paragraph for the morning brief

API: https://api.x.ai/v1/chat/completions (OpenAI-compatible)
Docs: https://docs.x.ai

Note on SuperGrok: SuperGrok subscription and xAI API credits are
separate accounts. Check console.x.ai for API access. Grok 4 Fast is the
recommended model for this tool (high throughput, low cost).
"""

import json
from dataclasses import dataclass, field
from typing import Optional

import requests

import config


GROK_API_URL = "https://api.x.ai/v1/chat/completions"


@dataclass
class GrokVerdict:
    ticker: str
    verdict: str         # CONFIRM_LONG, CONFIRM_SHORT, MIXED, NO_SIGNAL, FADE
    confidence: float    # 0-10
    narrative: str       # What's the story
    key_posts: list = field(default_factory=list)  # Notable X posts
    influencers: list = field(default_factory=list)  # Notable accounts posting
    risks: list = field(default_factory=list)       # Counter-narratives
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "verdict": self.verdict,
            "confidence": round(self.confidence, 1),
            "narrative": self.narrative,
            "key_posts": self.key_posts[:3],
            "influencers": self.influencers[:5],
            "risks": self.risks[:3],
        }


class GrokAnalyst:
    """Wrapper around xAI API with domain-specific prompts for trading use."""

    def __init__(self, api_key: Optional[str] = None,
                 model: Optional[str] = None):
        self.api_key = api_key or config.GROK_API_KEY
        self.model = model or getattr(config, "GROK_MODEL", "grok-4-fast")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _call(self, messages: list, use_live_search: bool = True,
              max_tokens: int = 800) -> Optional[str]:
        """Low-level API call. Returns content string or None on failure."""
        if not self.enabled:
            return None

        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }

        if use_live_search:
            body["search_parameters"] = {
                "mode": "on",
                "sources": [
                    {"type": "x"},
                    {"type": "web"},
                    {"type": "news"},
                ],
                "return_citations": True,
            }

        try:
            resp = requests.post(
                GROK_API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=45,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[Grok] API error: {e}")
            return None

    # ─── 1. Spike Validation ────────────────────────────────────────

    def validate_spike(self, ticker: str, price: float, pct_change: float,
                       volume_ratio: float) -> Optional[GrokVerdict]:
        """
        Given a spike candidate, search X + web to determine WHY it's moving.
        Returns GrokVerdict with narrative, key posts, and confidence.
        """
        prompt = f"""You are an equity research analyst. Stock ${ticker} is up {pct_change:+.1f}% today on {volume_ratio:.1f}x average volume at ${price:.2f}.

Using your live X (Twitter) and web search, answer:

1. WHAT is driving the move? Be specific about the catalyst.
2. WHO is posting about it? Flag any hedge fund managers, noted analysts, or accounts with >100k followers.
3. Is the narrative SUSTAINABLE or a flash pump?
4. Key risks or counter-narratives to be aware of.

Respond ONLY in valid JSON with this exact structure:
{{
  "verdict": "CONFIRM_LONG" | "CONFIRM_SHORT" | "MIXED" | "NO_SIGNAL" | "FADE",
  "confidence": 0-10,
  "narrative": "2-3 sentence explanation of what's driving the move",
  "key_posts": ["quote or summary of 2-3 notable X posts"],
  "influencers": ["@handle (follower count) - what they said"],
  "risks": ["counter-narrative or risk factor"]
}}"""

        content = self._call(
            [{"role": "user", "content": prompt}],
            use_live_search=True,
            max_tokens=800,
        )
        if not content:
            return None

        try:
            # Strip code fences if present
            text = content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())

            return GrokVerdict(
                ticker=ticker,
                verdict=data.get("verdict", "NO_SIGNAL"),
                confidence=float(data.get("confidence", 5)),
                narrative=data.get("narrative", ""),
                key_posts=data.get("key_posts", []),
                influencers=data.get("influencers", []),
                risks=data.get("risks", []),
                raw_response=content,
            )
        except Exception as e:
            print(f"[Grok] Parse error for {ticker}: {e}")
            return GrokVerdict(
                ticker=ticker, verdict="NO_SIGNAL", confidence=0,
                narrative=content[:500], raw_response=content,
            )

    # ─── 2. Position Health Check ───────────────────────────────────

    def check_position(self, ticker: str, entry_price: float,
                       current_price: float, pnl_pct: float) -> Optional[GrokVerdict]:
        """
        Daily sentiment drift check on an open holding.
        Returns verdict on whether to HOLD, TRIM, or EXIT based on narrative.
        """
        direction = "up" if pnl_pct >= 0 else "down"
        prompt = f"""You are a portfolio manager reviewing your position in ${ticker}.

Entry: ${entry_price:.2f}
Current: ${current_price:.2f}
P&L: {pnl_pct:+.1f}% ({direction})

Using your live X and web search, review:
1. Has sentiment shifted (bullish → bearish or vice versa) in the last 48h?
2. Are there NEW catalysts (positive or negative) we should act on?
3. Is the original thesis still intact, eroding, or broken?
4. Recommendation: HOLD, ADD, TRIM, or EXIT.

Respond ONLY in valid JSON:
{{
  "verdict": "HOLD" | "ADD" | "TRIM" | "EXIT",
  "confidence": 0-10,
  "narrative": "2-3 sentence situational assessment",
  "key_posts": ["notable X posts influencing sentiment"],
  "risks": ["top 2-3 risks to monitor"]
}}"""

        content = self._call(
            [{"role": "user", "content": prompt}],
            use_live_search=True,
            max_tokens=600,
        )
        if not content:
            return None

        try:
            text = content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())
            return GrokVerdict(
                ticker=ticker,
                verdict=data.get("verdict", "HOLD"),
                confidence=float(data.get("confidence", 5)),
                narrative=data.get("narrative", ""),
                key_posts=data.get("key_posts", []),
                risks=data.get("risks", []),
                raw_response=content,
            )
        except Exception as e:
            print(f"[Grok] Position check parse error for {ticker}: {e}")
            return GrokVerdict(
                ticker=ticker, verdict="HOLD", confidence=0,
                narrative=content[:500], raw_response=content,
            )

    # ─── 3. Brief Narrative Writer ──────────────────────────────────

    def write_narrative(self, macro_snapshot: dict,
                        spike_candidates: list,
                        portfolio: dict) -> str:
        """
        Generate the PM-style TL;DR paragraph for the morning brief.
        Synthesizes macro + radar + X chatter into a coherent narrative.
        """
        macro_summary = ", ".join(
            f"{name} {d.get('day_chg', 0):+.1f}%"
            for name, d in list(macro_snapshot.items())[:6]
        )
        top_candidates = ", ".join(
            f"${c.get('ticker', '?')} ({c.get('pct_change', 0):+.0f}%)"
            for c in spike_candidates[:5]
        )
        holdings = ", ".join(portfolio.keys()) if portfolio else "(cash)"

        prompt = f"""You are writing the TL;DR for a portfolio manager's morning brief. Keep it punchy and actionable — 4 short paragraphs max.

Market snapshot: {macro_summary}
Top spike candidates today: {top_candidates}
Current holdings: {holdings}

Using your live X and web search, write:

1. **Macro mood** (1-2 sentences): What's the market tone today? Risk-on or risk-off?
2. **Today's narrative** (2 sentences): What themes/stories are trending on X financial Twitter?
3. **Top ideas** (3 bullets): Your top 3 tactical ideas for today from the spike candidates.
4. **Watch for** (1 sentence): The biggest risk or catalyst to monitor.

Write in direct, confident PM voice. No hedging. No disclaimers. Use $TICKER format for stocks."""

        content = self._call(
            [{"role": "user", "content": prompt}],
            use_live_search=True,
            max_tokens=500,
        )
        return content or "_Grok narrative unavailable (API offline or no key)._"

    # ─── 4. Free-form Live Search ───────────────────────────────────

    def live_search(self, query: str, max_tokens: int = 500) -> str:
        """Raw live search — useful for ad-hoc questions."""
        content = self._call(
            [{"role": "user", "content": query}],
            use_live_search=True,
            max_tokens=max_tokens,
        )
        return content or ""


# ─── Convenience Functions ───────────────────────────────────────────

_default_analyst: Optional[GrokAnalyst] = None


def get_analyst() -> GrokAnalyst:
    global _default_analyst
    if _default_analyst is None:
        _default_analyst = GrokAnalyst()
    return _default_analyst


def enrich_spike_candidates(candidates: list, top_n: int = 10) -> list:
    """
    Run Grok spike validation on the top N candidates.
    Mutates each candidate to add .grok_verdict attribute.
    Returns the same list for chaining.
    """
    analyst = get_analyst()
    if not analyst.enabled:
        print("[Grok] Disabled (no API key) — skipping enrichment")
        return candidates

    for i, c in enumerate(candidates[:top_n]):
        print(f"  [Grok {i+1}/{top_n}] Validating ${c.ticker}...")
        verdict = analyst.validate_spike(
            ticker=c.ticker,
            price=c.price,
            pct_change=c.pct_change,
            volume_ratio=c.volume_ratio,
        )
        c.grok_verdict = verdict
        if verdict:
            # Boost or penalise spike_score based on Grok confidence
            if verdict.verdict == "CONFIRM_LONG":
                c.spike_score = min(100, c.spike_score + verdict.confidence * 2)
                c.reasoning.append(
                    f"🤖 Grok CONFIRM ({verdict.confidence:.0f}/10): {verdict.narrative[:100]}"
                )
            elif verdict.verdict == "FADE":
                c.spike_score = max(0, c.spike_score - verdict.confidence * 2)
                c.reasoning.append(f"🤖 Grok FADE: {verdict.narrative[:100]}")
            elif verdict.verdict == "MIXED":
                c.reasoning.append(f"🤖 Grok MIXED: {verdict.narrative[:100]}")

    # Re-sort by updated score
    candidates.sort(key=lambda c: c.spike_score, reverse=True)
    return candidates


def review_portfolio(positions: dict) -> dict[str, GrokVerdict]:
    """Run Grok check on each open position. Returns ticker -> verdict."""
    analyst = get_analyst()
    if not analyst.enabled or not positions:
        return {}

    results = {}
    for ticker, pos in positions.items():
        verdict = analyst.check_position(
            ticker=ticker,
            entry_price=pos.get("avg_entry", pos.get("current_price", 0)),
            current_price=pos.get("current_price", 0),
            pnl_pct=pos.get("pnl_pct", 0),
        )
        if verdict:
            results[ticker] = verdict
    return results
