"""
AI Analyst Module — Claude and Grok analysis engines.

In emulator mode: uses rule-based heuristics that mimic AI reasoning.
In live mode: calls Claude API and Grok API for real analysis.

Each analyst returns a score (0-10), action (BUY/SELL/HOLD), and reasoning.
"""

import json
from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config
from strategies import Signal


@dataclass
class AIAnalysis:
    model: str           # "claude" or "grok"
    ticker: str
    action: str          # BUY, SELL, HOLD
    score: float         # 0-10 confidence
    reasoning: str       # Natural language explanation
    factors: dict        # Structured breakdown


def claude_analyse(ticker: str, df: pd.DataFrame, price: float,
                   signals: list[Signal], info: dict) -> AIAnalysis:
    """
    Claude's analysis — fundamentals-focused, risk-aware.
    In emulator mode, uses heuristic rules. In live mode, calls Claude API.
    """
    if config.CLAUDE_API_KEY and not config.EMULATOR_MODE:
        return _claude_api_call(ticker, df, price, signals, info)
    return _claude_emulated(ticker, df, price, signals, info)


def grok_analyse(ticker: str, df: pd.DataFrame, price: float,
                 signals: list[Signal], info: dict) -> AIAnalysis:
    """
    Grok's analysis — sentiment-focused, momentum-aware.
    In emulator mode, uses heuristic rules. In live mode, calls Grok API.
    """
    if config.GROK_API_KEY and not config.EMULATOR_MODE:
        return _grok_api_call(ticker, df, price, signals, info)
    return _grok_emulated(ticker, df, price, signals, info)


# ─── Emulated Claude (Fundamentals + Risk Focus) ──────────────────────

def _claude_emulated(ticker: str, df: pd.DataFrame, price: float,
                     signals: list[Signal], info: dict) -> AIAnalysis:
    """Emulate Claude's analytical style: careful, fundamental, risk-aware."""
    score = 5.0  # Neutral starting point
    reasons = []
    factors = {}

    # 1. Aggregate quantitative signal scores
    buy_signals = [s for s in signals if s.action == "BUY"]
    sell_signals = [s for s in signals if s.action == "SELL"]
    avg_signal_score = sum(s.score for s in signals) / len(signals) if signals else 0

    if len(buy_signals) >= 2:
        score += 1.5
        reasons.append(f"{len(buy_signals)} strategies signal BUY — multi-strategy confirmation")
    elif len(sell_signals) >= 2:
        score -= 1.5
        reasons.append(f"{len(sell_signals)} strategies signal SELL — multi-strategy warning")

    factors["strategy_agreement"] = f"{len(buy_signals)} buy, {len(sell_signals)} sell"

    # 2. Fundamental checks
    pe = info.get("pe_ratio")
    if pe is not None:
        if pe < 15:
            score += 0.5
            reasons.append(f"Attractive P/E ratio ({pe:.1f}) — potential value")
        elif pe > 40:
            score -= 0.5
            reasons.append(f"High P/E ({pe:.1f}) — expensive, limits upside")
        factors["pe_ratio"] = pe

    div_yield = info.get("dividend_yield")
    if div_yield and div_yield > 0.03:
        score += 0.3
        reasons.append(f"Decent dividend yield ({div_yield*100:.1f}%) — income support")
        factors["dividend_yield"] = f"{div_yield*100:.1f}%"

    # 3. Risk assessment
    beta = info.get("beta", 1.0) or 1.0
    if beta > 1.5:
        score -= 0.5
        reasons.append(f"High beta ({beta:.2f}) — elevated risk")
    factors["beta"] = beta

    # 4. 52-week range context
    high_52w = info.get("52w_high")
    low_52w = info.get("52w_low")
    if high_52w and low_52w and high_52w > low_52w:
        range_pct = (price - low_52w) / (high_52w - low_52w) * 100
        if range_pct < 30:
            score += 0.5
            reasons.append(f"Near 52-week low ({range_pct:.0f}% of range) — potential recovery")
        elif range_pct > 90:
            score -= 0.5
            reasons.append(f"Near 52-week high ({range_pct:.0f}% of range) — limited upside")
        factors["52w_range_position"] = f"{range_pct:.0f}%"

    # 5. Trend consistency check
    if df is not None and len(df) >= 20:
        recent_returns = df["Close"].pct_change().tail(10)
        positive_days = (recent_returns > 0).sum()
        if positive_days >= 7:
            score += 0.5
            reasons.append(f"Consistent upward movement ({positive_days}/10 positive days)")
        elif positive_days <= 3:
            score -= 0.5
            reasons.append(f"Persistent weakness ({positive_days}/10 positive days)")
        factors["recent_positive_days"] = f"{positive_days}/10"

    # Clamp score
    score = max(0, min(10, score))
    action = "BUY" if score >= 7 else ("SELL" if score <= 3 else "HOLD")

    # Build comprehensive reasoning
    reasoning = (
        f"[Claude Analysis] {info.get('name', ticker)} @ {price:.2f}p\n"
        f"Overall confidence: {score:.1f}/10 → {action}\n\n"
        f"Key factors:\n" + "\n".join(f"  • {r}" for r in reasons)
    )

    return AIAnalysis(
        model="claude", ticker=ticker, action=action,
        score=score, reasoning=reasoning, factors=factors,
    )


# ─── Emulated Grok (Sentiment + Momentum Focus) ──────────────────────

def _grok_emulated(ticker: str, df: pd.DataFrame, price: float,
                   signals: list[Signal], info: dict) -> AIAnalysis:
    """Emulate Grok's analytical style: sentiment-driven, momentum-focused, bolder."""
    score = 5.0
    reasons = []
    factors = {}

    # 1. Momentum signals weighted more heavily
    momentum_signals = [s for s in signals if s.strategy == "momentum"]
    breakout_signals = [s for s in signals if s.strategy == "breakout"]

    for s in momentum_signals:
        if s.action == "BUY":
            score += 2.0
            reasons.append(f"Momentum BUY signal (score {s.score}) — trend is your friend")
        elif s.action == "SELL":
            score -= 2.0
            reasons.append(f"Momentum SELL signal — don't fight the trend")
    factors["momentum_signal"] = momentum_signals[0].action if momentum_signals else "N/A"

    for s in breakout_signals:
        if s.action == "BUY":
            score += 1.5
            reasons.append(f"Breakout detected — new highs attract buyers")
    factors["breakout_signal"] = breakout_signals[0].action if breakout_signals else "N/A"

    # 2. Social sentiment proxy (emulated via volume + price action)
    if df is not None and len(df) >= 5:
        vol_ratio = df["Vol_Ratio"].iloc[-1] if "Vol_Ratio" in df.columns else 1
        daily_return = df["Close"].pct_change().iloc[-1] * 100

        if vol_ratio > 2 and daily_return > 1:
            score += 1.5
            reasons.append(f"High volume surge ({vol_ratio:.1f}x) with positive move — social buzz likely")
            factors["sentiment_proxy"] = "bullish_volume_surge"
        elif vol_ratio > 2 and daily_return < -1:
            score -= 1.0
            reasons.append(f"High volume sell-off — possible negative sentiment")
            factors["sentiment_proxy"] = "bearish_volume_surge"
        else:
            factors["sentiment_proxy"] = "neutral"

    # 3. Short-term price patterns (Grok = more aggressive on quick moves)
    if df is not None and len(df) >= 5:
        last_3_returns = df["Close"].pct_change().tail(3)
        consecutive_up = (last_3_returns > 0).all()
        consecutive_down = (last_3_returns < 0).all()

        if consecutive_up:
            score += 1.0
            reasons.append("3 consecutive green days — momentum building")
        elif consecutive_down:
            # Grok is contrarian on extreme selloffs
            score += 0.5
            reasons.append("3 consecutive red days — contrarian bounce opportunity")
            factors["contrarian_flag"] = True

    # 4. Sector momentum (simplified)
    sector = info.get("sector", "Unknown")
    if sector in ["Technology", "Energy"]:
        score += 0.3
        reasons.append(f"{sector} sector tends to have strong momentum plays")
    factors["sector"] = sector

    # 5. Grok's edge: risk appetite is higher
    rsi = df["RSI"].iloc[-1] if df is not None and "RSI" in df.columns else 50
    if 55 <= rsi <= 70:
        score += 0.5
        reasons.append(f"RSI {rsi:.0f} — bullish momentum zone, not yet overbought")
    factors["rsi"] = f"{rsi:.1f}"

    score = max(0, min(10, score))
    action = "BUY" if score >= 6.5 else ("SELL" if score <= 3.5 else "HOLD")

    reasoning = (
        f"[Grok Analysis] {info.get('name', ticker)} @ {price:.2f}p\n"
        f"Sentiment score: {score:.1f}/10 → {action}\n\n"
        f"Key factors:\n" + "\n".join(f"  • {r}" for r in reasons)
    )

    return AIAnalysis(
        model="grok", ticker=ticker, action=action,
        score=score, reasoning=reasoning, factors=factors,
    )


# ─── Live API Calls (for future use) ─────────────────────────────────

def _claude_api_call(ticker: str, df: pd.DataFrame, price: float,
                     signals: list[Signal], info: dict) -> AIAnalysis:
    """Call Claude API for real analysis. Requires CLAUDE_API_KEY."""
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=config.CLAUDE_API_KEY)

        # Build context for Claude
        signal_summary = "\n".join(
            f"- {s.strategy}: {s.action} (score {s.score}, confidence {s.confidence:.0%})"
            for s in signals
        )
        technicals = ""
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            technicals = (
                f"RSI: {latest.get('RSI', 'N/A'):.1f}, "
                f"MACD Hist: {latest.get('MACD_Hist', 'N/A'):.4f}, "
                f"BB Position: price {'above' if price > latest.get('BB_Mid', price) else 'below'} mid band"
            )

        prompt = f"""Analyse this stock for a short-term trade (1-5 day hold) in a UK ISA account:

Ticker: {ticker} ({info.get('name', 'Unknown')})
Current Price: £{price:.2f}
Sector: {info.get('sector', 'Unknown')}
P/E Ratio: {info.get('pe_ratio', 'N/A')}
Beta: {info.get('beta', 'N/A')}
52-week range position: {info.get('52w_low', 'N/A')} - {info.get('52w_high', 'N/A')}

Technical Indicators: {technicals}

Strategy Signals:
{signal_summary}

Respond in JSON format:
{{"action": "BUY|SELL|HOLD", "score": 0-10, "reasoning": "explanation", "factors": {{}}}}"""

        message = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        result = json.loads(message.content[0].text)
        return AIAnalysis(
            model="claude", ticker=ticker,
            action=result["action"], score=result["score"],
            reasoning=result["reasoning"], factors=result.get("factors", {}),
        )
    except Exception as e:
        print(f"[Claude API] Error: {e}, falling back to emulated")
        return _claude_emulated(ticker, df, price, signals, info)


def _grok_api_call(ticker: str, df: pd.DataFrame, price: float,
                   signals: list[Signal], info: dict) -> AIAnalysis:
    """Call Grok API for real analysis. Requires GROK_API_KEY."""
    try:
        import requests

        signal_summary = "\n".join(
            f"- {s.strategy}: {s.action} (score {s.score})" for s in signals
        )

        prompt = f"""You are a trading analyst. Analyse this UK stock for short-term momentum trading:

Ticker: {ticker} ({info.get('name', 'Unknown')})
Price: £{price:.2f}, Sector: {info.get('sector', 'Unknown')}

Strategy Signals:
{signal_summary}

Focus on: social sentiment, momentum, crowd psychology, and short-term price action.
Respond in JSON: {{"action": "BUY|SELL|HOLD", "score": 0-10, "reasoning": "explanation", "factors": {{}}}}"""

        response = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {config.GROK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.GROK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
            },
            timeout=30,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
        return AIAnalysis(
            model="grok", ticker=ticker,
            action=result["action"], score=result["score"],
            reasoning=result["reasoning"], factors=result.get("factors", {}),
        )
    except Exception as e:
        print(f"[Grok API] Error: {e}, falling back to emulated")
        return _grok_emulated(ticker, df, price, signals, info)
