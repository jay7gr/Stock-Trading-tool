"""
Trading Strategies — quantitative signal generation.
Each strategy scores a stock from -10 (strong sell) to +10 (strong buy)
and returns a reasoning string explaining the decision.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass


@dataclass
class Signal:
    ticker: str
    strategy: str
    action: str          # "BUY", "SELL", "HOLD"
    score: float         # -10 to +10
    confidence: float    # 0 to 1
    entry_price: float
    stop_loss: float
    take_profit: float
    reasoning: str


def momentum_strategy(ticker: str, df: pd.DataFrame, price: float) -> Signal:
    """
    Momentum / Trend-Following Strategy.
    - Buy when price is above SMA_20, MACD bullish, RSI 40-70
    - Sell when price drops below SMA_20 or RSI > 75
    """
    if df is None or len(df) < 50:
        return _hold_signal(ticker, "momentum", price, "Insufficient data")

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    score = 0
    reasons = []

    # Trend: Price vs moving averages
    if price > latest.get("SMA_20", price):
        score += 2
        reasons.append("Price above SMA20 (uptrend)")
    else:
        score -= 2
        reasons.append("Price below SMA20 (downtrend)")

    if price > latest.get("SMA_50", price):
        score += 1
        reasons.append("Price above SMA50 (strong trend)")
    else:
        score -= 1
        reasons.append("Price below SMA50 (weak trend)")

    # MACD
    macd_hist = latest.get("MACD_Hist", 0)
    prev_macd_hist = prev.get("MACD_Hist", 0)
    if macd_hist > 0 and macd_hist > prev_macd_hist:
        score += 2
        reasons.append(f"MACD histogram rising ({macd_hist:.4f})")
    elif macd_hist < 0:
        score -= 2
        reasons.append(f"MACD histogram negative ({macd_hist:.4f})")

    # RSI
    rsi = latest.get("RSI", 50)
    if 40 <= rsi <= 65:
        score += 1
        reasons.append(f"RSI in healthy range ({rsi:.1f})")
    elif rsi > 75:
        score -= 2
        reasons.append(f"RSI overbought ({rsi:.1f}) — risk of pullback")
    elif rsi < 30:
        score -= 1
        reasons.append(f"RSI oversold ({rsi:.1f}) — momentum strategy avoids")

    # Volume confirmation
    vol_ratio = latest.get("Vol_Ratio", 1)
    if vol_ratio > 1.5:
        score += 1
        reasons.append(f"Above-average volume ({vol_ratio:.1f}x)")
    elif vol_ratio < 0.5:
        score -= 1
        reasons.append(f"Low volume ({vol_ratio:.1f}x) — weak conviction")

    # Rate of change
    roc = latest.get("ROC_5", 0)
    if roc > 3:
        score += 1
        reasons.append(f"Strong 5-day momentum (+{roc:.1f}%)")
    elif roc < -3:
        score -= 1
        reasons.append(f"Negative 5-day momentum ({roc:.1f}%)")

    action = "BUY" if score >= 3 else ("SELL" if score <= -3 else "HOLD")
    atr = latest.get("ATR", price * 0.02)
    stop_loss = price - 2 * atr if action == "BUY" else 0
    take_profit = price + 3 * atr if action == "BUY" else 0
    confidence = min(abs(score) / 10, 1.0)

    return Signal(
        ticker=ticker, strategy="momentum", action=action,
        score=score, confidence=confidence,
        entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
        reasoning=" | ".join(reasons),
    )


def mean_reversion_strategy(ticker: str, df: pd.DataFrame, price: float) -> Signal:
    """
    Mean Reversion Strategy.
    - Buy when price is near/below lower Bollinger Band and RSI < 35
    - Sell when price returns to middle band or RSI > 60
    """
    if df is None or len(df) < 30:
        return _hold_signal(ticker, "mean_reversion", price, "Insufficient data")

    latest = df.iloc[-1]
    score = 0
    reasons = []

    # Bollinger Band position
    bb_lower = latest.get("BB_Lower", price * 0.96)
    bb_mid = latest.get("BB_Mid", price)
    bb_upper = latest.get("BB_Upper", price * 1.04)

    if price <= bb_lower:
        score += 3
        reasons.append(f"Price at/below lower Bollinger Band ({price:.2f} <= {bb_lower:.2f})")
    elif price <= bb_mid:
        score += 1
        reasons.append("Price below middle Bollinger Band")
    elif price >= bb_upper:
        score -= 2
        reasons.append(f"Price at/above upper Bollinger Band — overextended")

    # RSI for oversold/overbought
    rsi = latest.get("RSI", 50)
    if rsi < 30:
        score += 3
        reasons.append(f"RSI deeply oversold ({rsi:.1f}) — reversion likely")
    elif rsi < 40:
        score += 1
        reasons.append(f"RSI approaching oversold ({rsi:.1f})")
    elif rsi > 70:
        score -= 2
        reasons.append(f"RSI overbought ({rsi:.1f}) — sell signal for mean reversion")

    # Stochastic oversold
    stoch_k = latest.get("Stoch_K", 50)
    if stoch_k < 20:
        score += 2
        reasons.append(f"Stochastic oversold ({stoch_k:.1f})")
    elif stoch_k > 80:
        score -= 1
        reasons.append(f"Stochastic overbought ({stoch_k:.1f})")

    # Price distance from SMA20 (mean)
    sma20 = latest.get("SMA_20", price)
    pct_from_mean = (price - sma20) / sma20 * 100
    if pct_from_mean < -3:
        score += 2
        reasons.append(f"Price {pct_from_mean:.1f}% below SMA20 — stretched")
    elif pct_from_mean > 3:
        score -= 1
        reasons.append(f"Price {pct_from_mean:.1f}% above SMA20")

    action = "BUY" if score >= 4 else ("SELL" if score <= -3 else "HOLD")
    atr = latest.get("ATR", price * 0.02)
    stop_loss = price - 1.5 * atr if action == "BUY" else 0
    take_profit = sma20 if action == "BUY" else 0  # Target = return to mean
    confidence = min(abs(score) / 10, 1.0)

    return Signal(
        ticker=ticker, strategy="mean_reversion", action=action,
        score=score, confidence=confidence,
        entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
        reasoning=" | ".join(reasons),
    )


def breakout_strategy(ticker: str, df: pd.DataFrame, price: float) -> Signal:
    """
    Breakout Strategy.
    - Buy when price breaks above recent resistance with volume
    - Uses 20-day high as resistance level
    """
    if df is None or len(df) < 25:
        return _hold_signal(ticker, "breakout", price, "Insufficient data")

    latest = df.iloc[-1]
    score = 0
    reasons = []

    # Resistance = 20-day high (excluding today)
    recent_high = df["High"].iloc[-21:-1].max()
    recent_low = df["Low"].iloc[-21:-1].min()

    if price > recent_high:
        score += 3
        reasons.append(f"BREAKOUT: Price ({price:.2f}) above 20-day high ({recent_high:.2f})")
    elif price > recent_high * 0.99:
        score += 1
        reasons.append(f"Price approaching 20-day high ({recent_high:.2f})")
    elif price < recent_low:
        score -= 3
        reasons.append(f"BREAKDOWN: Price below 20-day low ({recent_low:.2f})")

    # Volume must confirm breakout
    vol_ratio = latest.get("Vol_Ratio", 1)
    if price > recent_high and vol_ratio > 1.5:
        score += 2
        reasons.append(f"Breakout confirmed by volume ({vol_ratio:.1f}x average)")
    elif price > recent_high and vol_ratio < 1:
        score -= 1
        reasons.append(f"Breakout on low volume ({vol_ratio:.1f}x) — suspect")

    # Bollinger Band squeeze (low volatility precedes breakouts)
    bb_width = latest.get("BB_Width", 0.05)
    if bb_width < 0.03:
        score += 1
        reasons.append("Bollinger Band squeeze — volatility expansion expected")

    # ATR expansion
    atr = latest.get("ATR", price * 0.02)
    atr_prev = df["ATR"].iloc[-5] if "ATR" in df.columns and len(df) >= 5 else atr
    if atr > atr_prev * 1.2:
        score += 1
        reasons.append("ATR expanding — increasing volatility confirms move")

    action = "BUY" if score >= 4 else ("SELL" if score <= -3 else "HOLD")
    stop_loss = recent_high * 0.98 if action == "BUY" else 0  # Stop just below breakout level
    take_profit = price + 2 * atr if action == "BUY" else 0
    confidence = min(abs(score) / 10, 1.0)

    return Signal(
        ticker=ticker, strategy="breakout", action=action,
        score=score, confidence=confidence,
        entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
        reasoning=" | ".join(reasons),
    )


def etf_rotation_strategy(ticker: str, df: pd.DataFrame, price: float) -> Signal:
    """
    ETF Relative Strength / Rotation Strategy.
    - Buy ETFs with strongest recent momentum
    - Simpler strategy for lower-risk allocation
    """
    if df is None or len(df) < 30:
        return _hold_signal(ticker, "etf_rotation", price, "Insufficient data")

    latest = df.iloc[-1]
    score = 0
    reasons = []

    # 1-month return
    if len(df) >= 22:
        month_return = (price / df["Close"].iloc[-22] - 1) * 100
        if month_return > 3:
            score += 3
            reasons.append(f"Strong 1-month return (+{month_return:.1f}%)")
        elif month_return > 0:
            score += 1
            reasons.append(f"Positive 1-month return (+{month_return:.1f}%)")
        else:
            score -= 2
            reasons.append(f"Negative 1-month return ({month_return:.1f}%)")

    # Price above SMA50
    sma50 = latest.get("SMA_50", price)
    if price > sma50:
        score += 2
        reasons.append("Price above SMA50 — medium-term uptrend")
    else:
        score -= 2
        reasons.append("Price below SMA50 — medium-term downtrend")

    # Low volatility preferred for ETF rotation
    atr_pct = latest.get("ATR", 0) / price * 100
    if atr_pct < 1.5:
        score += 1
        reasons.append(f"Low volatility ({atr_pct:.2f}% ATR)")

    action = "BUY" if score >= 3 else ("SELL" if score <= -2 else "HOLD")
    atr = latest.get("ATR", price * 0.015)
    stop_loss = price - 2.5 * atr if action == "BUY" else 0
    take_profit = price + 4 * atr if action == "BUY" else 0
    confidence = min(abs(score) / 10, 1.0)

    return Signal(
        ticker=ticker, strategy="etf_rotation", action=action,
        score=score, confidence=confidence,
        entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
        reasoning=" | ".join(reasons),
    )


def generate_all_signals(ticker: str, df: pd.DataFrame, price: float) -> list[Signal]:
    """Run all strategies on a ticker and return list of signals."""
    is_etf = ticker.endswith((".L",)) and any(
        etf in ticker for etf in ["VUSA", "VWRL", "ISF", "SWDA", "VUKE"]
    )

    signals = [
        momentum_strategy(ticker, df, price),
        mean_reversion_strategy(ticker, df, price),
        breakout_strategy(ticker, df, price),
    ]

    if is_etf:
        signals.append(etf_rotation_strategy(ticker, df, price))

    return signals


def _hold_signal(ticker: str, strategy: str, price: float, reason: str) -> Signal:
    return Signal(
        ticker=ticker, strategy=strategy, action="HOLD",
        score=0, confidence=0, entry_price=price,
        stop_loss=0, take_profit=0, reasoning=reason,
    )
