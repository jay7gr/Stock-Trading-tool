"""
AI Analyst Module — two independent analysis engines using pure local logic.

No external API calls. All analysis runs locally using technical indicators,
fundamentals, and price action rules.

Engine A ("Claude"): Conservative, fundamental & risk-focused.
Engine B ("Grok"): Aggressive, momentum & sentiment-proxy-focused.

Each returns a score (0-10), action (BUY/SELL/HOLD), and detailed reasoning.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass

from strategies import Signal


@dataclass
class AIAnalysis:
    model: str           # "fundamentals" or "momentum"
    ticker: str
    action: str          # BUY, SELL, HOLD
    score: float         # 0-10 confidence
    reasoning: str       # Natural language explanation
    factors: dict        # Structured breakdown


def claude_analyse(ticker: str, df: pd.DataFrame, price: float,
                   signals: list[Signal], info: dict) -> AIAnalysis:
    """Engine A: Fundamentals & Risk analysis. Conservative."""
    return _fundamentals_engine(ticker, df, price, signals, info)


def grok_analyse(ticker: str, df: pd.DataFrame, price: float,
                 signals: list[Signal], info: dict) -> AIAnalysis:
    """Engine B: Momentum & Sentiment-proxy analysis. Aggressive."""
    return _momentum_engine(ticker, df, price, signals, info)


# ─── Engine A: Fundamentals & Risk ────────────────────────────────────

def _fundamentals_engine(ticker: str, df: pd.DataFrame, price: float,
                         signals: list[Signal], info: dict) -> AIAnalysis:
    """
    Analyses stocks like a value investor / risk manager.
    Checks: valuation, yield, volatility, trend health, strategy consensus,
    support/resistance levels, volume profile, and drawdown risk.
    """
    score = 5.0
    reasons = []
    factors = {}

    # ── 1. Strategy consensus (multi-strategy confirmation) ──
    buy_signals = [s for s in signals if s.action == "BUY"]
    sell_signals = [s for s in signals if s.action == "SELL"]
    avg_score = sum(s.score for s in signals) / len(signals) if signals else 0

    if len(buy_signals) >= 3:
        score += 2.0
        reasons.append(f"{len(buy_signals)}/{len(signals)} strategies signal BUY — strong multi-strategy confirmation")
    elif len(buy_signals) >= 2:
        score += 1.0
        reasons.append(f"{len(buy_signals)} strategies signal BUY — moderate agreement")
    elif len(sell_signals) >= 3:
        score -= 2.0
        reasons.append(f"{len(sell_signals)}/{len(signals)} strategies signal SELL — broad weakness")
    elif len(sell_signals) >= 2:
        score -= 1.0
        reasons.append(f"{len(sell_signals)} strategies signal SELL — caution warranted")

    # Weight by average signal strength
    if avg_score > 4:
        score += 0.5
        reasons.append(f"Average signal score strong ({avg_score:.1f})")
    elif avg_score < -2:
        score -= 0.5
        reasons.append(f"Average signal score weak ({avg_score:.1f})")

    factors["strategy_consensus"] = f"{len(buy_signals)} buy, {len(sell_signals)} sell, avg {avg_score:.1f}"

    # ── 2. Valuation (P/E ratio) ──
    pe = info.get("pe_ratio")
    if pe is not None:
        if pe < 10:
            score += 1.0
            reasons.append(f"Very attractive P/E ({pe:.1f}) — deep value territory")
        elif pe < 15:
            score += 0.5
            reasons.append(f"Attractive P/E ({pe:.1f}) — reasonably valued")
        elif pe > 50:
            score -= 1.0
            reasons.append(f"Extremely high P/E ({pe:.1f}) — speculative valuation")
        elif pe > 30:
            score -= 0.5
            reasons.append(f"High P/E ({pe:.1f}) — growth priced in, limited margin of safety")
        factors["pe_ratio"] = round(pe, 1)

    # ── 3. Dividend yield (income support / quality signal) ──
    div_yield = info.get("dividend_yield")
    if div_yield is not None:
        if div_yield > 0.05:
            score += 0.5
            reasons.append(f"Strong dividend yield ({div_yield*100:.1f}%) — income support + quality signal")
        elif div_yield > 0.03:
            score += 0.3
            reasons.append(f"Decent dividend yield ({div_yield*100:.1f}%)")
        elif div_yield == 0:
            reasons.append("No dividend — growth-only thesis required")
        factors["dividend_yield"] = f"{div_yield*100:.1f}%"

    # ── 4. Volatility & risk (beta + ATR) ──
    beta = info.get("beta") or 1.0
    if beta > 1.8:
        score -= 1.0
        reasons.append(f"Very high beta ({beta:.2f}) — amplified market risk")
    elif beta > 1.3:
        score -= 0.5
        reasons.append(f"Elevated beta ({beta:.2f}) — above-average volatility")
    elif beta < 0.5:
        score += 0.3
        reasons.append(f"Low beta ({beta:.2f}) — defensive characteristics")
    factors["beta"] = round(beta, 2)

    if df is not None and "ATR" in df.columns and len(df) > 0:
        atr = df["ATR"].iloc[-1]
        atr_pct = atr / price * 100
        if atr_pct > 3:
            score -= 0.5
            reasons.append(f"High daily volatility ({atr_pct:.1f}% ATR) — wide swings")
        elif atr_pct < 1:
            score += 0.3
            reasons.append(f"Low volatility ({atr_pct:.1f}% ATR) — stable")
        factors["atr_pct"] = f"{atr_pct:.2f}%"

    # ── 5. 52-week range context ──
    high_52w = info.get("52w_high")
    low_52w = info.get("52w_low")
    if high_52w and low_52w and high_52w > low_52w:
        range_pct = (price - low_52w) / (high_52w - low_52w) * 100
        if range_pct < 20:
            score += 1.0
            reasons.append(f"Near 52-week low ({range_pct:.0f}% of range) — potential deep value recovery")
        elif range_pct < 35:
            score += 0.5
            reasons.append(f"Lower third of 52-week range ({range_pct:.0f}%) — value zone")
        elif range_pct > 95:
            score -= 0.5
            reasons.append(f"At 52-week high ({range_pct:.0f}%) — limited upside, elevated risk")
        elif range_pct > 80:
            score -= 0.3
            reasons.append(f"Upper 52-week range ({range_pct:.0f}%) — approaching resistance")
        factors["52w_position"] = f"{range_pct:.0f}%"

    # ── 6. Trend health (moving average alignment) ──
    if df is not None and len(df) >= 50:
        latest = df.iloc[-1]
        sma10 = latest.get("SMA_10", price)
        sma20 = latest.get("SMA_20", price)
        sma50 = latest.get("SMA_50", price)

        if price > sma10 > sma20 > sma50:
            score += 1.0
            reasons.append("Perfect MA alignment (price > SMA10 > SMA20 > SMA50) — healthy uptrend")
            factors["trend"] = "strong_uptrend"
        elif price > sma20 > sma50:
            score += 0.5
            reasons.append("Price above SMA20 and SMA50 — uptrend intact")
            factors["trend"] = "uptrend"
        elif price < sma10 < sma20 < sma50:
            score -= 1.0
            reasons.append("Bearish MA alignment (price < all MAs) — downtrend")
            factors["trend"] = "downtrend"
        elif price < sma20:
            score -= 0.5
            reasons.append("Price below SMA20 — short-term weakness")
            factors["trend"] = "weak"
        else:
            factors["trend"] = "mixed"

    # ── 7. Recent consistency ──
    if df is not None and len(df) >= 20:
        recent = df["Close"].pct_change().tail(10)
        positive_days = (recent > 0).sum()
        avg_daily = recent.mean() * 100

        if positive_days >= 7:
            score += 0.5
            reasons.append(f"Consistent gains ({positive_days}/10 green days, avg {avg_daily:+.2f}%/day)")
        elif positive_days <= 3:
            score -= 0.5
            reasons.append(f"Persistent selling ({positive_days}/10 green days, avg {avg_daily:+.2f}%/day)")
        factors["recent_positive_days"] = f"{positive_days}/10"
        factors["avg_daily_return"] = f"{avg_daily:+.3f}%"

    # ── 8. Support/resistance proximity ──
    if df is not None and len(df) >= 20:
        recent_low = df["Low"].tail(20).min()
        recent_high = df["High"].tail(20).max()
        dist_to_support = (price - recent_low) / price * 100
        dist_to_resistance = (recent_high - price) / price * 100

        if dist_to_support < 1.5:
            score += 0.5
            reasons.append(f"Near 20-day support ({dist_to_support:.1f}% above) — good risk/reward entry")
        if dist_to_resistance < 0.5:
            score -= 0.3
            reasons.append(f"At 20-day resistance ({dist_to_resistance:.1f}% below) — breakout or rejection")
        factors["dist_to_support"] = f"{dist_to_support:.1f}%"
        factors["dist_to_resistance"] = f"{dist_to_resistance:.1f}%"

    # ── 9. Drawdown risk assessment ──
    if df is not None and len(df) >= 30:
        cum_returns = (1 + df["Close"].pct_change()).cumprod()
        rolling_max = cum_returns.expanding().max()
        drawdown = (cum_returns - rolling_max) / rolling_max
        current_dd = drawdown.iloc[-1] * 100
        max_dd = drawdown.min() * 100

        if current_dd < -10:
            score -= 0.5
            reasons.append(f"Currently in significant drawdown ({current_dd:.1f}%) — catching a falling knife risk")
        elif current_dd < -5:
            reasons.append(f"Moderate drawdown ({current_dd:.1f}%) — monitor for recovery signals")
        factors["current_drawdown"] = f"{current_dd:.1f}%"
        factors["max_drawdown_60d"] = f"{max_dd:.1f}%"

    # ── 10. Volume trend (institutional interest) ──
    if df is not None and "Vol_SMA_20" in df.columns and len(df) >= 20:
        vol_trend = df["Volume"].tail(5).mean() / df["Vol_SMA_20"].iloc[-1] if df["Vol_SMA_20"].iloc[-1] > 0 else 1
        if vol_trend > 1.3:
            score += 0.3
            reasons.append(f"Rising volume trend ({vol_trend:.1f}x avg) — increasing interest")
        elif vol_trend < 0.6:
            score -= 0.3
            reasons.append(f"Declining volume ({vol_trend:.1f}x avg) — fading interest")
        factors["volume_trend"] = f"{vol_trend:.1f}x"

    # Clamp and decide
    score = max(0, min(10, score))
    action = "BUY" if score >= 7 else ("SELL" if score <= 3 else "HOLD")

    reasoning = (
        f"[Fundamentals Engine] {info.get('name', ticker)} @ £{price:,.2f}\n"
        f"Risk-adjusted score: {score:.1f}/10 → {action}\n\n"
        f"Analysis:\n" + "\n".join(f"  • {r}" for r in reasons)
    )

    return AIAnalysis(
        model="fundamentals", ticker=ticker, action=action,
        score=score, reasoning=reasoning, factors=factors,
    )


# ─── Engine B: Momentum & Sentiment Proxy ─────────────────────────────

def _momentum_engine(ticker: str, df: pd.DataFrame, price: float,
                     signals: list[Signal], info: dict) -> AIAnalysis:
    """
    Analyses stocks like a momentum/swing trader.
    Checks: trend strength, MACD dynamics, RSI zones, volume spikes,
    price acceleration, breakout proximity, mean reversion setups,
    and multi-timeframe momentum.
    """
    score = 5.0
    reasons = []
    factors = {}

    if df is None or len(df) < 20:
        return AIAnalysis(
            model="momentum", ticker=ticker, action="HOLD",
            score=5.0, reasoning="Insufficient data for momentum analysis",
            factors={},
        )

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else latest

    # ── 1. Momentum signal weighting (momentum + breakout strategies) ──
    for s in signals:
        if s.strategy == "momentum":
            if s.action == "BUY" and s.score >= 5:
                score += 2.0
                reasons.append(f"Strong momentum BUY (score {s.score}) — powerful trend")
            elif s.action == "BUY":
                score += 1.0
                reasons.append(f"Momentum BUY (score {s.score}) — trend is your friend")
            elif s.action == "SELL":
                score -= 2.0
                reasons.append(f"Momentum SELL (score {s.score}) — don't fight the trend")
            factors["momentum_signal"] = f"{s.action} ({s.score})"

        elif s.strategy == "breakout":
            if s.action == "BUY" and s.score >= 5:
                score += 1.5
                reasons.append(f"Breakout confirmed (score {s.score}) — new highs attract buyers")
            elif s.action == "BUY":
                score += 0.5
                reasons.append("Possible breakout forming")
            factors["breakout_signal"] = f"{s.action} ({s.score})"

    # ── 2. MACD dynamics (trend acceleration) ──
    macd_hist = latest.get("MACD_Hist", 0)
    prev_macd = prev.get("MACD_Hist", 0)
    macd = latest.get("MACD", 0)
    macd_signal = latest.get("MACD_Signal", 0)

    if macd_hist > 0 and macd_hist > prev_macd:
        score += 0.5
        reasons.append(f"MACD histogram rising ({macd_hist:.4f}) — accelerating bullish momentum")
        factors["macd_trend"] = "bullish_accelerating"
    elif macd_hist > 0 and macd_hist < prev_macd:
        reasons.append("MACD histogram positive but fading — momentum slowing")
        factors["macd_trend"] = "bullish_fading"
    elif macd_hist < 0 and macd_hist < prev_macd:
        score -= 0.5
        reasons.append(f"MACD histogram falling ({macd_hist:.4f}) — accelerating bearish momentum")
        factors["macd_trend"] = "bearish_accelerating"
    elif macd_hist < 0 and macd_hist > prev_macd:
        score += 0.3
        reasons.append("MACD histogram recovering — selling pressure easing")
        factors["macd_trend"] = "bearish_recovering"

    # MACD crossover detection
    if macd > macd_signal and prev.get("MACD", 0) <= prev.get("MACD_Signal", 0):
        score += 1.0
        reasons.append("MACD bullish crossover — buy signal")
        factors["macd_crossover"] = "bullish"
    elif macd < macd_signal and prev.get("MACD", 0) >= prev.get("MACD_Signal", 0):
        score -= 1.0
        reasons.append("MACD bearish crossover — sell signal")
        factors["macd_crossover"] = "bearish"

    # ── 3. RSI analysis (momentum zones + divergence) ──
    rsi = latest.get("RSI", 50)
    if 55 <= rsi <= 70:
        score += 0.5
        reasons.append(f"RSI {rsi:.0f} — bullish momentum zone, room to run")
    elif 40 <= rsi <= 55:
        reasons.append(f"RSI {rsi:.0f} — neutral, waiting for direction")
    elif rsi > 80:
        score -= 1.0
        reasons.append(f"RSI {rsi:.0f} — severely overbought, pullback imminent")
    elif rsi > 70:
        score -= 0.5
        reasons.append(f"RSI {rsi:.0f} — overbought, momentum exhaustion risk")
    elif rsi < 25:
        score += 1.0
        reasons.append(f"RSI {rsi:.0f} — deeply oversold, contrarian bounce setup")
    elif rsi < 35:
        score += 0.5
        reasons.append(f"RSI {rsi:.0f} — oversold, potential bounce")
    factors["rsi"] = f"{rsi:.1f}"

    # RSI trend (is RSI itself trending up?)
    if len(df) >= 5 and "RSI" in df.columns:
        rsi_5ago = df["RSI"].iloc[-5]
        if not np.isnan(rsi_5ago):
            rsi_change = rsi - rsi_5ago
            if rsi_change > 10:
                score += 0.3
                reasons.append(f"RSI trending up strongly (+{rsi_change:.0f} in 5 days)")
            elif rsi_change < -10:
                score -= 0.3
                reasons.append(f"RSI trending down ({rsi_change:.0f} in 5 days)")
            factors["rsi_5d_change"] = f"{rsi_change:+.1f}"

    # ── 4. Volume spike analysis (sentiment proxy) ──
    vol_ratio = latest.get("Vol_Ratio", 1)
    daily_return = df["Close"].pct_change().iloc[-1] * 100

    if vol_ratio > 2.5 and daily_return > 1.5:
        score += 1.5
        reasons.append(f"Massive volume spike ({vol_ratio:.1f}x) with strong up move (+{daily_return:.1f}%) — institutional buying")
        factors["sentiment_proxy"] = "strong_bullish"
    elif vol_ratio > 1.8 and daily_return > 0.5:
        score += 0.8
        reasons.append(f"High volume ({vol_ratio:.1f}x) with positive move — buyers in control")
        factors["sentiment_proxy"] = "bullish"
    elif vol_ratio > 2 and daily_return < -1.5:
        score -= 1.0
        reasons.append(f"High volume sell-off ({vol_ratio:.1f}x, {daily_return:.1f}%) — capitulation or panic")
        factors["sentiment_proxy"] = "bearish_capitulation"
    elif vol_ratio > 1.5 and daily_return < -0.5:
        score -= 0.5
        reasons.append(f"Elevated volume on decline — distribution")
        factors["sentiment_proxy"] = "distribution"
    else:
        factors["sentiment_proxy"] = "neutral"
    factors["volume_ratio"] = f"{vol_ratio:.1f}x"

    # ── 5. Consecutive day patterns ──
    if len(df) >= 5:
        last_returns = df["Close"].pct_change().tail(5)
        consec_up = 0
        consec_down = 0
        for r in last_returns:
            if r > 0:
                consec_up += 1
                consec_down = 0
            elif r < 0:
                consec_down += 1
                consec_up = 0

        if consec_up >= 4:
            score += 0.5
            reasons.append(f"{consec_up} consecutive green days — strong buying momentum")
        elif consec_up >= 3:
            score += 0.3
            reasons.append(f"{consec_up} green days running — momentum building")
        elif consec_down >= 4:
            # Contrarian: extreme selloff = bounce opportunity
            score += 0.5
            reasons.append(f"{consec_down} consecutive red days — contrarian bounce opportunity (oversold)")
            factors["contrarian_flag"] = True
        elif consec_down >= 3:
            score -= 0.3
            reasons.append(f"{consec_down} red days running — sustained selling pressure")

        factors["consecutive_up"] = consec_up
        factors["consecutive_down"] = consec_down

    # ── 6. Price acceleration (rate of change comparison) ──
    roc5 = latest.get("ROC_5", 0)
    roc10 = latest.get("ROC_10", 0)

    if roc5 > 5:
        score += 0.5
        reasons.append(f"Strong 5-day surge (+{roc5:.1f}%) — explosive momentum")
    elif roc5 > 2 and roc5 > roc10:
        score += 0.3
        reasons.append(f"Accelerating: 5-day ROC ({roc5:.1f}%) > 10-day ROC ({roc10:.1f}%)")
    elif roc5 < -5:
        score -= 0.3
        reasons.append(f"Sharp 5-day decline ({roc5:.1f}%)")
    factors["roc_5d"] = f"{roc5:.1f}%"
    factors["roc_10d"] = f"{roc10:.1f}%"

    # ── 7. Stochastic analysis ──
    stoch_k = latest.get("Stoch_K", 50)
    stoch_d = latest.get("Stoch_D", 50)

    if stoch_k < 20 and stoch_k > stoch_d:
        score += 0.5
        reasons.append(f"Stochastic bullish crossover in oversold zone ({stoch_k:.0f}) — buy signal")
    elif stoch_k > 80 and stoch_k < stoch_d:
        score -= 0.5
        reasons.append(f"Stochastic bearish crossover in overbought zone ({stoch_k:.0f}) — sell signal")
    factors["stochastic"] = f"K={stoch_k:.0f}, D={stoch_d:.0f}"

    # ── 8. Bollinger Band squeeze (volatility contraction → expansion) ──
    bb_width = latest.get("BB_Width", 0.05)
    if bb_width < 0.025:
        score += 0.5
        reasons.append(f"Bollinger squeeze ({bb_width:.3f}) — major move imminent, volatility expansion expected")
        factors["bb_squeeze"] = True
    elif bb_width > 0.08:
        reasons.append(f"Wide Bollinger Bands ({bb_width:.3f}) — high volatility, trend in progress")
    factors["bb_width"] = f"{bb_width:.3f}"

    # ── 9. Sector momentum bias ──
    sector = info.get("sector", "Unknown")
    momentum_sectors = ["Technology", "Energy", "Mining"]
    defensive_sectors = ["Consumer", "Healthcare", "Utilities"]

    if sector in momentum_sectors:
        score += 0.3
        reasons.append(f"{sector} sector — tends to have strong momentum characteristics")
    elif sector in defensive_sectors:
        score -= 0.2
        reasons.append(f"{sector} sector — typically lower momentum, steadier moves")
    factors["sector"] = sector

    # ── 10. Multi-timeframe check (short vs medium trend agreement) ──
    if len(df) >= 50:
        sma10 = latest.get("SMA_10", price)
        sma50 = latest.get("SMA_50", price)

        short_trend = "up" if price > sma10 else "down"
        medium_trend = "up" if price > sma50 else "down"

        if short_trend == "up" and medium_trend == "up":
            score += 0.5
            reasons.append("Multi-timeframe alignment: both short and medium trends bullish")
            factors["timeframe_alignment"] = "both_bullish"
        elif short_trend == "down" and medium_trend == "down":
            score -= 0.5
            reasons.append("Multi-timeframe alignment: both trends bearish")
            factors["timeframe_alignment"] = "both_bearish"
        elif short_trend == "up" and medium_trend == "down":
            reasons.append("Mixed signals: short-term bounce within medium-term downtrend")
            factors["timeframe_alignment"] = "mixed_bouncing"
        else:
            reasons.append("Mixed signals: short-term dip within medium-term uptrend")
            factors["timeframe_alignment"] = "mixed_pullback"

    # Clamp and decide (momentum engine has slightly lower threshold)
    score = max(0, min(10, score))
    action = "BUY" if score >= 6.5 else ("SELL" if score <= 3.5 else "HOLD")

    reasoning = (
        f"[Momentum Engine] {info.get('name', ticker)} @ £{price:,.2f}\n"
        f"Momentum score: {score:.1f}/10 → {action}\n\n"
        f"Analysis:\n" + "\n".join(f"  • {r}" for r in reasons)
    )

    return AIAnalysis(
        model="momentum", ticker=ticker, action=action,
        score=score, reasoning=reasoning, factors=factors,
    )
