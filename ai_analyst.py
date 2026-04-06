"""
AI Analyst Module — real Claude and Grok analysis when API keys are set,
falls back to local rule-based engines when not.

Claude API: Deep fundamental analysis, risk assessment, macro reasoning.
Grok API:   Momentum analysis, X/Twitter sentiment, crowd psychology.
Local:      Rule-based scoring as fallback (no API cost).
"""

import json
import numpy as np
import pandas as pd
import requests
from dataclasses import dataclass
from typing import Optional

import config
from strategies import Signal


@dataclass
class AIAnalysis:
    model: str           # "claude", "grok", "local_conservative", "local_aggressive"
    ticker: str
    action: str          # BUY, SELL, HOLD
    score: float         # 0-10 confidence
    reasoning: str       # Natural language explanation
    factors: dict        # Structured breakdown


def claude_analyse(ticker: str, df: pd.DataFrame, price: float,
                   signals: list[Signal], info: dict) -> AIAnalysis:
    """Analyse with Claude API if available, otherwise local engine."""
    if config.CLAUDE_API_KEY:
        result = _claude_api_call(ticker, df, price, signals, info)
        if result:
            return result
    return _local_conservative(ticker, df, price, signals, info)


def grok_analyse(ticker: str, df: pd.DataFrame, price: float,
                 signals: list[Signal], info: dict) -> AIAnalysis:
    """Analyse with Grok API if available, otherwise local engine."""
    if config.GROK_API_KEY:
        result = _grok_api_call(ticker, df, price, signals, info)
        if result:
            return result
    return _local_aggressive(ticker, df, price, signals, info)


# ─── Claude API (Real Analysis) ───────────────────────────────────────

def _build_context(ticker: str, df: pd.DataFrame, price: float,
                   signals: list[Signal], info: dict) -> str:
    """Build a rich context string for AI analysis."""
    signal_summary = "\n".join(
        f"  - {s.strategy}: {s.action} (score {s.score:.1f}, "
        f"confidence {s.confidence:.0%})"
        for s in signals
    )

    technicals = ""
    if df is not None and len(df) > 0:
        latest = df.iloc[-1]
        technicals = (
            f"  RSI: {latest.get('RSI', 'N/A'):.1f}\n"
            f"  MACD Histogram: {latest.get('MACD_Hist', 'N/A'):.4f}\n"
            f"  Bollinger Band position: price {'above' if price > latest.get('BB_Mid', price) else 'below'} middle band\n"
            f"  ATR (volatility): {latest.get('ATR', 'N/A'):.2f}\n"
            f"  Volume vs 20d avg: {latest.get('Vol_Ratio', 1):.1f}x\n"
            f"  SMA20: {latest.get('SMA_20', 'N/A'):.2f}, SMA50: {latest.get('SMA_50', 'N/A'):.2f}\n"
            f"  Stochastic K/D: {latest.get('Stoch_K', 50):.0f}/{latest.get('Stoch_D', 50):.0f}\n"
            f"  5-day ROC: {latest.get('ROC_5', 0):.1f}%"
        )

    # Recent price action
    price_action = ""
    if df is not None and len(df) >= 10:
        last_5 = df["Close"].pct_change().tail(5) * 100
        price_action = (
            f"  Last 5 days returns: {', '.join(f'{r:+.1f}%' for r in last_5)}\n"
            f"  5-day cumulative: {last_5.sum():+.1f}%"
        )

    return f"""Stock: {ticker} ({info.get('name', 'Unknown')})
Current Price: £{price:,.2f}
Sector: {info.get('sector', 'Unknown')}

Fundamentals:
  P/E Ratio: {info.get('pe_ratio', 'N/A')}
  Dividend Yield: {info.get('dividend_yield', 'N/A')}
  Beta: {info.get('beta', 'N/A')}
  52-week High: {info.get('52w_high', 'N/A')}
  52-week Low: {info.get('52w_low', 'N/A')}

Technical Indicators:
{technicals}

Recent Price Action:
{price_action}

Quantitative Strategy Signals:
{signal_summary}

Portfolio Context:
  Account type: UK Stocks & Shares ISA (long-only, no leverage)
  Target hold period: 1-5 trading days
  Risk per trade: 2% stop-loss, 4% take-profit"""


def _claude_api_call(ticker: str, df: pd.DataFrame, price: float,
                     signals: list[Signal], info: dict) -> Optional[AIAnalysis]:
    """Call Claude API for real fundamental and risk analysis."""
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=config.CLAUDE_API_KEY)

        context = _build_context(ticker, df, price, signals, info)

        message = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": f"""You are a professional equity analyst for UK stocks. Analyse this stock for a short-term trade (1-5 day hold) in a UK ISA account.

{context}

Consider:
1. Is the valuation attractive or stretched?
2. What are the key risks (sector, macro, company-specific)?
3. Do the technical signals align with the fundamental picture?
4. What could go wrong with this trade?
5. Overall: should we buy, sell, or hold?

Respond ONLY in this exact JSON format:
{{"action": "BUY" or "SELL" or "HOLD", "score": 0-10, "reasoning": "2-3 sentence explanation", "factors": {{"valuation": "cheap/fair/expensive", "risk_level": "low/medium/high", "trend": "bullish/bearish/neutral", "key_risk": "one sentence"}}}}"""
            }],
        )

        text = message.content[0].text.strip()
        # Extract JSON from response (handle markdown code blocks)
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)

        return AIAnalysis(
            model="claude",
            ticker=ticker,
            action=result["action"],
            score=float(result["score"]),
            reasoning=f"[Claude AI] {info.get('name', ticker)} @ £{price:,.2f}\n{result['reasoning']}",
            factors=result.get("factors", {}),
        )
    except Exception as e:
        print(f"[Claude API] Error for {ticker}: {e}")
        return None


# ─── Grok API (Real Sentiment Analysis) ───────────────────────────────

def _grok_api_call(ticker: str, df: pd.DataFrame, price: float,
                   signals: list[Signal], info: dict) -> Optional[AIAnalysis]:
    """Call Grok API for sentiment and momentum analysis."""
    try:
        context = _build_context(ticker, df, price, signals, info)

        response = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {config.GROK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.GROK_MODEL,
                "messages": [{
                    "role": "user",
                    "content": f"""You are a momentum trader and sentiment analyst. Analyse this UK stock for a short-term swing trade.

{context}

Focus on:
1. Is momentum building or fading? What does the MACD and RSI suggest?
2. Is there likely social/news sentiment driving the stock?
3. Is this a good entry point for a momentum or contrarian play?
4. Volume patterns — are institutions accumulating or distributing?

Respond ONLY in this exact JSON format:
{{"action": "BUY" or "SELL" or "HOLD", "score": 0-10, "reasoning": "2-3 sentence explanation", "factors": {{"momentum": "strong/moderate/weak/negative", "sentiment": "bullish/neutral/bearish", "volume_signal": "accumulation/distribution/neutral", "timing": "good/wait/late"}}}}"""
                }],
                "max_tokens": 600,
            },
            timeout=30,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"].strip()

        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)

        return AIAnalysis(
            model="grok",
            ticker=ticker,
            action=result["action"],
            score=float(result["score"]),
            reasoning=f"[Grok AI] {info.get('name', ticker)} @ £{price:,.2f}\n{result['reasoning']}",
            factors=result.get("factors", {}),
        )
    except Exception as e:
        print(f"[Grok API] Error for {ticker}: {e}")
        return None


# ─── Local Fallback Engines (No API Cost) ─────────────────────────────

def _local_conservative(ticker: str, df: pd.DataFrame, price: float,
                        signals: list[Signal], info: dict) -> AIAnalysis:
    """Conservative rule-based engine. Used when Claude API key not set."""
    score = 5.0
    reasons = []
    factors = {}

    buy_signals = [s for s in signals if s.action == "BUY"]
    sell_signals = [s for s in signals if s.action == "SELL"]

    if len(buy_signals) >= 3:
        score += 2.0
        reasons.append(f"{len(buy_signals)} strategies signal BUY")
    elif len(buy_signals) >= 2:
        score += 1.0
    if len(sell_signals) >= 2:
        score -= 1.5
        reasons.append(f"{len(sell_signals)} strategies signal SELL")

    factors["strategy_consensus"] = f"{len(buy_signals)} buy, {len(sell_signals)} sell"

    pe = info.get("pe_ratio")
    if pe is not None:
        if pe < 12:
            score += 0.8
            reasons.append(f"Attractive P/E ({pe:.1f})")
        elif pe > 35:
            score -= 0.5
            reasons.append(f"High P/E ({pe:.1f})")
        factors["pe_ratio"] = round(pe, 1)

    div_yield = info.get("dividend_yield")
    if div_yield and div_yield > 0.03:
        score += 0.3
        reasons.append(f"Dividend yield {div_yield*100:.1f}%")

    beta = info.get("beta") or 1.0
    if beta > 1.5:
        score -= 0.5
    factors["beta"] = round(beta, 2)

    if df is not None and len(df) >= 50:
        latest = df.iloc[-1]
        sma20 = latest.get("SMA_20", price)
        sma50 = latest.get("SMA_50", price)
        if price > sma20 > sma50:
            score += 0.8
            reasons.append("Healthy uptrend (MA aligned)")
        elif price < sma20 < sma50:
            score -= 0.8
            reasons.append("Downtrend (below all MAs)")

    if df is not None and len(df) >= 10:
        pos_days = (df["Close"].pct_change().tail(10) > 0).sum()
        if pos_days >= 7:
            score += 0.5
        elif pos_days <= 3:
            score -= 0.5
        factors["positive_days"] = f"{pos_days}/10"

    score = max(0, min(10, score))
    action = "BUY" if score >= 7 else ("SELL" if score <= 3 else "HOLD")

    reasoning = (
        f"[Local Conservative] {info.get('name', ticker)} @ £{price:,.2f}\n"
        f"Score: {score:.1f}/10 → {action}\n"
        + "\n".join(f"  • {r}" for r in reasons)
        + "\n\n⚠ Local rules only — add Claude API key for real AI analysis"
    )

    return AIAnalysis(
        model="local_conservative", ticker=ticker, action=action,
        score=score, reasoning=reasoning, factors=factors,
    )


def _local_aggressive(ticker: str, df: pd.DataFrame, price: float,
                      signals: list[Signal], info: dict) -> AIAnalysis:
    """Aggressive rule-based engine. Used when Grok API key not set."""
    score = 5.0
    reasons = []
    factors = {}

    for s in signals:
        if s.strategy == "momentum" and s.action == "BUY":
            score += 2.0
            reasons.append(f"Momentum BUY (score {s.score})")
        elif s.strategy == "momentum" and s.action == "SELL":
            score -= 2.0
            reasons.append("Momentum SELL")
        if s.strategy == "breakout" and s.action == "BUY":
            score += 1.5
            reasons.append("Breakout detected")

    if df is not None and len(df) >= 5:
        latest = df.iloc[-1]

        vol_ratio = latest.get("Vol_Ratio", 1)
        daily_ret = df["Close"].pct_change().iloc[-1] * 100
        if vol_ratio > 2 and daily_ret > 1:
            score += 1.5
            reasons.append(f"Volume spike ({vol_ratio:.1f}x) + up move")
            factors["sentiment_proxy"] = "bullish"
        elif vol_ratio > 2 and daily_ret < -1:
            score -= 1.0
            factors["sentiment_proxy"] = "bearish"
        else:
            factors["sentiment_proxy"] = "neutral"

        rsi = latest.get("RSI", 50)
        if 55 <= rsi <= 70:
            score += 0.5
            reasons.append(f"RSI {rsi:.0f} — bullish zone")
        elif rsi > 75:
            score -= 0.5
        elif rsi < 30:
            score += 0.5
            reasons.append(f"RSI {rsi:.0f} — oversold bounce")
        factors["rsi"] = f"{rsi:.1f}"

        # Consecutive patterns
        last_rets = df["Close"].pct_change().tail(5)
        consec_up = sum(1 for r in last_rets if r > 0)
        if consec_up >= 4:
            score += 0.5
            reasons.append(f"{consec_up} green days — momentum")

    score = max(0, min(10, score))
    action = "BUY" if score >= 6.5 else ("SELL" if score <= 3.5 else "HOLD")

    reasoning = (
        f"[Local Aggressive] {info.get('name', ticker)} @ £{price:,.2f}\n"
        f"Score: {score:.1f}/10 → {action}\n"
        + "\n".join(f"  • {r}" for r in reasons)
        + "\n\n⚠ Local rules only — add Grok API key for real sentiment analysis"
    )

    return AIAnalysis(
        model="local_aggressive", ticker=ticker, action=action,
        score=score, reasoning=reasoning, factors=factors,
    )
