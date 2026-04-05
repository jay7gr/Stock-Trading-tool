# AI Trading Tool

Automated dual-AI (Claude + Grok) trading tool for UK Stocks & Shares ISA.
Paper trading emulator with real market data, Streamlit dashboard, and VUSA benchmark comparison.

## Quick Start — GitHub Codespaces (Recommended)

1. Go to the repo on GitHub
2. Click **Code** → **Codespaces** → **Create codespace on claude/automated-trading-tool-pVFor**
3. Wait ~60 seconds for setup
4. The dashboard opens automatically at the forwarded port
5. Click **"Run Market Scan"** in the sidebar

## Quick Start — Local

```bash
git clone https://github.com/jay7gr/Stock-Trading-tool.git
cd Stock-Trading-tool
git checkout claude/automated-trading-tool-pVFor
pip install -r requirements.txt
streamlit run dashboard.py
```

## Quick Start — Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io)
2. Sign in with GitHub
3. Deploy: repo `jay7gr/Stock-Trading-tool`, branch `claude/automated-trading-tool-pVFor`, file `dashboard.py`
4. Get a permanent public URL

## What It Does

- Scans 25 liquid LSE stocks & ETFs using real market data (yfinance)
- Runs 4 quantitative strategies: momentum, mean reversion, breakout, ETF rotation
- Dual AI analysis: Claude (fundamentals/risk) + Grok (sentiment/momentum)
- Consensus engine merges both AI opinions before executing
- Paper trading emulator tracks positions, P&L, and trade history
- Risk manager enforces daily stop-loss and position limits
- Benchmark comparison vs VUSA (S&P 500) — daily, monthly, quarterly, annual

## Dashboard Tabs

| Tab | Shows |
|-----|-------|
| **Recommendations** | BUY/SELL/HOLD for each stock with scores, prices, and full reasoning |
| **Open Positions** | Live P&L, allocation pie chart |
| **Trade History** | Every trade with expandable reasoning |
| **Benchmark Comparison** | Portfolio vs VUSA charts and alpha calculation |
| **AI Analysis Detail** | Side-by-side Claude vs Grok reasoning |

## Configuration

Edit `config.py` to set:
- `INITIAL_CAPITAL` — starting capital (default £20,000)
- `DAILY_STOP_LOSS` — max daily loss before halting (default -£200)
- `DAILY_PROFIT_TARGET_MIN/MAX` — daily profit target (default £200-500)
- `CONSENSUS_MODE` — "specialised", "weighted", or "agreement"
- `CLAUDE_API_KEY` / `GROK_API_KEY` — for live AI analysis (optional)
- `T212_API_KEY` — for live Trading 212 execution (future)

## Architecture

```
Market Data (yfinance) → Strategies → Claude AI + Grok AI → Consensus Engine → Risk Manager → Paper Trader
```
