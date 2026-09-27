# Stock Trading Tool — Project Context

## What This Is
A PM-style trading intelligence tool for a UK Stocks & Shares ISA (Trading 212),
scanning the full Russell 1000 + 2000 (~3000 US stocks) for early-stage spike
signals and generating daily PM briefs with actionable trade ideas.

Capital: £20K. Long-only. No leverage. Tax-free gains (ISA).

## Architecture

```
Layer 1: Quant Radar (Python, free)
  └─ russell_universe.py → spike_radar.py → scoring (vol, price, SI, float, gap)
  └─ yfinance batch quotes (free, unlimited)

Layer 2: News + Social (Python, free)
  └─ news_feed.py: Finnhub + yfinance + SEC EDGAR
  └─ social_sentiment.py: StockTwits trending + Reddit r/wsb mention velocity

Layer 3: Grok Intelligence (SuperGrok subscription + optional xAI API)
  └─ grok_analyst.py: validate spikes via xAI API Live Search (X + web)
  └─ SuperGrok Tasks: 5 recurring prompts in docs/grok_tasks.md
  └─ Gmail integration: reads Grok Task email outputs from noreply@x.ai

Layer 4: Execution
  └─ broker_t212.py: Trading 212 REST API (free)
  └─ emulator.py: paper trading with JSON persistence

Layer 5: Output
  └─ pm_brief.py: morning brief (macro, portfolio, earnings, spikes, social)
  └─ notifications.py: Telegram alerts
  └─ dashboard.py: Streamlit UI
```

## Key Commands
```bash
python main.py radar              # One-shot spike scan of Russell
python main.py brief              # Generate PM daily brief
python main.py brief --skip-scan  # Brief without full radar (faster)
python main.py watch              # Continuous spike monitor + Telegram alerts
python main.py scan               # Legacy UK FTSE quant scan
streamlit run dashboard.py        # Web dashboard on localhost:8501
```

## API Keys Needed (config.py)
- FINNHUB_API_KEY: Free at finnhub.io (60 req/min) — unlocks news + earnings
- GROK_API_KEY: From console.x.ai — unlocks live X/web validation (~$1/mo)
- TELEGRAM_BOT_TOKEN + CHAT_ID: Free via @BotFather — unlocks push alerts
- T212_API_KEY: Trading 212 app → Settings → API — unlocks live execution

All optional. Tool works with zero keys (yfinance + StockTwits + Reddit only).

## User Preferences
- No paid APIs unless truly necessary (SuperGrok subscription is OK)
- Grok integration via SuperGrok Tasks (email → Gmail) preferred over API
- Trading 212 ISA for execution
- Wants to catch early-stage spikes like $OPEN (Eric Jackson tweet) and $BIRD
- Wants PM-style daily briefs, not just quant scores
- Two-tier approach: free broad scan → deep analysis on candidates only
- User is UK-based, trades US stocks via T212

## File Layout
- config.py: All settings, thresholds, API key slots
- russell_universe.py: Loads R1000 + R2000 from iShares CSV
- spike_radar.py: Core scanner with composite scoring
- news_feed.py: Finnhub + yfinance + SEC aggregator
- social_sentiment.py: StockTwits + Reddit mentions
- grok_analyst.py: xAI API wrapper (validate spikes, check positions, narrative)
- pm_brief.py: Morning brief generator
- trading_engine.py: Legacy UK quant orchestrator
- strategies.py: Momentum, mean reversion, breakout, ETF rotation
- ai_analyst.py: Conservative + aggressive local scoring engines
- consensus.py: Dual-engine consensus with specialised mode
- emulator.py: Paper trading; stops/targets checked on 1m bar High/Low (bar_checks.py);
  fills need a <=60s quote (quotes.py) and must match the ticket in instruments.py
- instruments.py: Instrument registry (ISIN, listing, currency, GBX/GBP/USD unit, yf + Freetrade tickers)
  + exchange sessions (LSE, XETRA, Euronext Paris/Amsterdam, NYSE, NASDAQ, TSE, HKEX, ASX; tz/DST aware)
- bar_checks.py: Bar-based stop/target rules (stop wins; gap fills at Open; bad-tick guard)
- quotes.py: Timestamped quotes, 1m bars, freshness enforcement, 429 backoff
- monitor.py: 1-min stop/target monitor loop (24x5, market-aware: network only for positions whose
  exchange is open + one final check after close; one -£200 day halt across all markets in GBP;
  halts on closed markets are queued and flattened at next open)
  + `--replay DATE --position SYM:entry:stop:target:opened`
  (runs via /workspace/dash/start_monitor.sh; pidfile /workspace/dash/monitor.pid)
- market_hours.py: `coverage(ticker)` pre-fill check (session data, market open, monitor heartbeat)
- pnl.py: day P&L (SOP v2.3: London day, realised today + change in open MTM since the previous
  close — entry for positions opened today; GBP) + GBP formatting for the dashboard/monitor.
  monitor.stamp_prev_close() sets Position.prev_close on the first pass of each London day.
- sizing.py: execution-time qty for Risk tickets (ticket qty = ceiling; effective risk/share =
  stop distance x 1.6 + FX 0.78% (non-GBP) + purchase tax, in GBP; refuses price <= stop).
  CLI: python sizing.py TSM --ticket-qty 1 --stop 429.5215 --entry 450.61 --price 451 --gbpusd 1.3253
- Levels: execute_buy(levels_ccy="USD") keeps non-GBP stops/targets as prices in the line's currency;
  default levels are GBP (GBX lines in POUNDS). Levels on the wrong side of the fill are refused.
- Yahoo .L data is ~20 min delayed (Yahoo help SLN2310; config.FEED_DELAY_MIN = {"LSE": 20}).
  Decision (27 Sep): NO prices from the Freetrade app. LSE paper legs are filled with lse_leg.py:
    python lse_leg.py VUSA --at 08:06 --ticket-qty 13 --stop 108.1686 --entry 110.4325 --target 114.9603 \
        --pending TSM:1:429.5215:450.61 --pending WMT:7:103.704:107.98 --pending MSFT:1:492.6326:516.17 \
        --gbpusd 1.325346 [--dry-run]
  Run it once the decision-minute bar is visible (~20-45 min later). Fill = that 1m bar's OPEN,
  fill time = quote_ts = bar start, quote_source "yahoo_1m_bar_open_delayed", booked_at = wall clock.
  No look-ahead (later bars discarded). Refused: no bar at that minute, bar open <= stop, as_of in
  the future / > 45 min old / outside the session, non-GBP line, stale heartbeat or LSE closed at
  the wall clock. --dry-run writes nothing (temp copy); --now (dry-run only) simulates the clock.
  emulator.execute_buy(as_of=..., quote=..., qty=...) is the underlying API (freshness vs as_of).
- Delayed-feed stop checks: bars count as complete only when start + 1m <= now - delay, and the
  monitor's LSE check window is shifted by the delay (checks ~08:20-16:55, final 16:55-17:05).
  Exits are booked at the true breach bar (stop price, or bar open if gapped), never at alert time.
- Package sizing (Risk ruling 27 Sep): sizing.package_qty / `python sizing.py package ...`.
  Ticket qty = ceiling; a leg fills while realised loss today + full effective risk of every open
  leg (day ref -> stop) + filling legs <= £200; cuts in whole shares from the lowest rank up
  (MSFT, WMT, SHEL, VUSA, TSM). US legs (real-time quotes) book with:
    python sizing.py package --leg TSM:1:429.5215:492.787 --leg WMT:7:103.704:116.532 \
        --leg MSFT:1:492.6326:563.2448 --book [--dry-run]
  lse_leg.py runs the same package check (with --pending legs reserved by rank).
- alerts.py: data/alerts_log.jsonl event log
- tests/: pytest suite (fixture: real ISPY.L 1m bars 2026-09-25)
- broker_t212.py: Trading 212 REST client
- notifications.py: Telegram + email alerts
- portfolio.py: Benchmark comparison vs VUSA
- dashboard.py: Streamlit UI (5 tabs)
- main.py: CLI entry point (radar / brief / watch / scan)
- docs/grok_tasks.md: 5 SuperGrok recurring task prompts
- docs/funnel.html: Architecture diagram
- docs/index.html: Static HTML dashboard

## Branch
Development: claude/automated-trading-tool-pVFor
Production: main (merge when ready)
