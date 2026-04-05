"""
Consensus Engine — merges Claude and Grok analyses into a final trade decision.

Supports three modes:
  - agreement: Both must agree on action
  - weighted: Weighted average of scores
  - specialised: Claude handles fundamentals/risk, Grok handles sentiment/momentum
"""

from dataclasses import dataclass
from ai_analyst import AIAnalysis
from strategies import Signal
import config


@dataclass
class TradeDecision:
    ticker: str
    action: str              # BUY, SELL, HOLD
    combined_score: float    # 0-10
    confidence: float        # 0-1
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size_gbp: float
    claude_analysis: AIAnalysis
    grok_analysis: AIAnalysis
    reasoning: str           # Full explanation of why this decision was made
    strategy_signals: list   # Underlying quant signals


def make_decision(
    ticker: str,
    price: float,
    signals: list[Signal],
    claude: AIAnalysis,
    grok: AIAnalysis,
    available_capital: float,
) -> TradeDecision:
    """Produce a final trade decision from dual-AI analysis."""

    mode = config.CONSENSUS_MODE

    if mode == "agreement":
        return _agreement_mode(ticker, price, signals, claude, grok, available_capital)
    elif mode == "weighted":
        return _weighted_mode(ticker, price, signals, claude, grok, available_capital)
    else:
        return _specialised_mode(ticker, price, signals, claude, grok, available_capital)


def _agreement_mode(ticker, price, signals, claude, grok, capital):
    """Both AI models must agree on the action."""
    if claude.action == grok.action and claude.action != "HOLD":
        action = claude.action
        combined_score = (claude.score + grok.score) / 2
        reasoning = (
            f"CONSENSUS (Agreement Mode): Both Claude and Grok agree → {action}\n\n"
            f"{claude.reasoning}\n\n{grok.reasoning}"
        )
    else:
        action = "HOLD"
        combined_score = 5.0
        reasoning = (
            f"NO CONSENSUS: Claude says {claude.action} ({claude.score:.1f}), "
            f"Grok says {grok.action} ({grok.score:.1f}) → HOLD\n\n"
            f"{claude.reasoning}\n\n{grok.reasoning}"
        )

    return _build_decision(ticker, price, signals, claude, grok, action,
                           combined_score, capital, reasoning)


def _weighted_mode(ticker, price, signals, claude, grok, capital):
    """Weighted average of scores. Claude 60%, Grok 40%."""
    combined_score = (claude.score * config.CLAUDE_WEIGHT +
                      grok.score * config.GROK_WEIGHT)

    if combined_score >= config.MIN_CONSENSUS_SCORE:
        action = "BUY"
    elif combined_score <= 3.0:
        action = "SELL"
    else:
        action = "HOLD"

    reasoning = (
        f"CONSENSUS (Weighted Mode): "
        f"Claude {claude.score:.1f} × {config.CLAUDE_WEIGHT} + "
        f"Grok {grok.score:.1f} × {config.GROK_WEIGHT} = "
        f"{combined_score:.1f} → {action}\n"
        f"(Threshold: {config.MIN_CONSENSUS_SCORE})\n\n"
        f"{claude.reasoning}\n\n{grok.reasoning}"
    )

    return _build_decision(ticker, price, signals, claude, grok, action,
                           combined_score, capital, reasoning)


def _specialised_mode(ticker, price, signals, claude, grok, capital):
    """
    Claude handles fundamentals/risk, Grok handles sentiment/momentum.
    Final decision uses both perspectives with different weights per dimension.
    """
    # Claude's risk assessment (is it safe to trade?)
    risk_ok = claude.score >= 4.5  # Claude must not see major risk

    # Grok's momentum assessment (is timing right?)
    momentum_ok = grok.score >= 5.5  # Grok must see positive momentum

    # Combined score weighted by specialisation
    combined_score = claude.score * 0.45 + grok.score * 0.55  # Slightly favour momentum

    if risk_ok and momentum_ok and combined_score >= config.MIN_CONSENSUS_SCORE:
        action = "BUY"
        reasoning = (
            f"CONSENSUS (Specialised Mode):\n"
            f"  Claude (Risk/Fundamentals): {claude.score:.1f}/10 — CLEARED ✓\n"
            f"  Grok (Sentiment/Momentum): {grok.score:.1f}/10 — CLEARED ✓\n"
            f"  Combined: {combined_score:.1f}/10 → BUY\n\n"
        )
    elif not risk_ok and not momentum_ok:
        action = "SELL" if combined_score < 3.5 else "HOLD"
        reasoning = (
            f"CONSENSUS (Specialised Mode):\n"
            f"  Claude (Risk): {claude.score:.1f}/10 — RISK WARNING ✗\n"
            f"  Grok (Momentum): {grok.score:.1f}/10 — WEAK ✗\n"
            f"  Combined: {combined_score:.1f}/10 → {action}\n\n"
        )
    else:
        action = "HOLD"
        risk_status = "CLEARED ✓" if risk_ok else "RISK WARNING ✗"
        mom_status = "CLEARED ✓" if momentum_ok else "WEAK ✗"
        reasoning = (
            f"CONSENSUS (Specialised Mode):\n"
            f"  Claude (Risk): {claude.score:.1f}/10 — {risk_status}\n"
            f"  Grok (Momentum): {grok.score:.1f}/10 — {mom_status}\n"
            f"  Combined: {combined_score:.1f}/10 → HOLD (partial agreement only)\n\n"
        )

    reasoning += f"{claude.reasoning}\n\n{grok.reasoning}"

    return _build_decision(ticker, price, signals, claude, grok, action,
                           combined_score, capital, reasoning)


def _build_decision(ticker, price, signals, claude, grok, action,
                    combined_score, capital, reasoning):
    """Build the final TradeDecision with position sizing."""

    # Find best signal for stop/take-profit levels
    buy_signals = [s for s in signals if s.action == "BUY"]
    if buy_signals:
        best = max(buy_signals, key=lambda s: s.score)
        stop_loss = best.stop_loss
        take_profit = best.take_profit
    else:
        stop_loss = price * (1 - config.DEFAULT_STOP_LOSS_PCT)
        take_profit = price * (1 + config.DEFAULT_TAKE_PROFIT_PCT)

    # Position sizing based on confidence and risk
    confidence = min(combined_score / 10, 1.0)
    if action == "BUY":
        # Scale position size with confidence: 5-15% of capital
        size_pct = 0.05 + (confidence - 0.5) * 0.2  # 5% at 0.5 conf, 15% at 1.0
        size_pct = max(0.05, min(size_pct, config.MAX_POSITION_PCT))
        position_size = capital * size_pct
        position_size = max(position_size, config.MIN_POSITION_SIZE)
    else:
        position_size = 0

    return TradeDecision(
        ticker=ticker, action=action,
        combined_score=combined_score, confidence=confidence,
        entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
        position_size_gbp=position_size,
        claude_analysis=claude, grok_analysis=grok,
        reasoning=reasoning, strategy_signals=signals,
    )
