# SuperGrok Recurring Tasks — Trading Intelligence Pack

Three pre-built recurring prompts designed to complement the Python tool.
Paste each into SuperGrok's **Tasks** feature and set the suggested schedule.

Base SuperGrok allows ~10 active tasks. Tasks run on xAI's servers with
Live Search access to X and the web — no API cost, uses your subscription.

## Setup

1. Open [grok.com](https://grok.com) or Grok on x.com
2. Click the **Tasks** icon (sidebar)
3. **New Task** → paste the prompt → set schedule → enable notifications
4. Results are saved to your Tasks tab and push to your phone

---

## 📅 Task 1: Pre-market Spike Radar

**Schedule:** Weekdays, 12:30 UK time (07:30 ET, 30 min before US open)
**Purpose:** Catch early-stage spikes before they run 50%+

```
You are a hedge fund analyst scanning for US stocks about to spike.

Using Live Search on X and the web, find 5-10 US stocks (any cap, NYSE/NASDAQ)
that show EARLY-STAGE spike signals for today. I want to catch moves like
$OPEN (Eric Jackson tweet, July 2025) or $BIRD (short squeeze narrative)
BEFORE they run 50%+.

Look for ANY of these catalysts in the last 24h:
1. A hedge fund manager or analyst with 50k+ followers posting a thesis on X
2. Unusual pre-market volume (>3x normal)
3. Short squeeze setup: short interest >20% + positive catalyst
4. Low-float stocks (<100M) getting social buzz on r/wallstreetbets, StockTwits
5. Overnight news (earnings beat, FDA approval, M&A, contract win, insider buy)
6. Activist investor 13D filings or hedge fund entries

Return as a markdown table with columns:
| Ticker | Price | Catalyst | Key Post/Source | Sentiment | Entry Plan | Risk |

Include the X @handle and follower count for any influencer mentions.
Flag the #1 pick with 🔥.
Keep it under 400 words. No disclaimers.
```

---

## 📅 Task 2: Portfolio Sentiment Monitor

**Schedule:** Weekdays, 13:00 UK time (08:00 ET)
**Purpose:** Detect sentiment drift on holdings before price reflects it

> Edit the ticker list below to match your Trading 212 holdings.

```
I hold these US stocks: [PASTE YOUR TICKERS, e.g. OPEN, SOFI, PLTR, RIVN]

Using Live Search on X and the web, for EACH ticker, check last 24h:

1. Sentiment shift (bullish → bearish or vice versa)
2. New catalysts (positive or negative)
3. Notable X posts from accounts >50k followers
4. Is the original thesis intact, eroding, or broken?
5. Recommendation: HOLD / ADD / TRIM / EXIT

Return as a table:
| Ticker | Sentiment (24h) | Catalyst | Verdict | Confidence (1-10) |

Below the table, write 3 bullets: top risks I should monitor today.
Be direct. No hedging. PM voice.
```

---

## 📅 Task 3: Weekly Hedge Fund Tracker

**Schedule:** Sundays, 20:00 UK time (15:00 ET)
**Purpose:** Surface ideas from the sharpest minds on X + spot cluster signals

```
Using Live Search on X, the web, and recent SEC filings, report what
the following hedge fund managers posted or filed about this week:

- Eric Jackson (@ericjackson)
- Scott Bessent / Key Square
- Bill Ackman (@BillAckman)
- Cathie Wood / ARK
- Chris Camillo
- Marc Cohodes (@AlderLaneEggs)
- Michael Burry / Scion (@michaeljburry)
- Dan Loeb / Third Point
- Jim Chanos (@RealJimChanos)

For each, report:
| Manager | New Position | Bullish Takes | Bearish Takes | Notable Thread |

Flag any stock mentioned by 2+ managers with 🎯 (cluster signal).
Include 13D/13G filings from this week if relevant.
Under 500 words.
```

---

## 📅 Task 4: Intraday Catalyst Check (Optional)

**Schedule:** Weekdays, 16:00 UK time (11:00 ET, mid-session)
**Purpose:** Mid-day scan for breaking catalysts

```
Using Live Search on X and the web, identify US stocks that have
experienced a breaking catalyst in the last 3 hours:

1. Unexpected news (SEC filing, FDA news, contract win, CEO departure)
2. Viral X thread from a known analyst
3. Unusual options activity reports
4. Trading halts and subsequent reopens
5. Sector-wide shifts (Fed comments, oil, geopolitics)

Return as a table:
| Ticker | Time | Catalyst | X Reaction | Trade Idea |

Only flag stocks where the catalyst is LESS THAN 3 HOURS OLD.
If nothing meaningful, say so — don't pad.
```

---

## 📅 Task 5: End-of-Day Review (Optional)

**Schedule:** Weekdays, 22:00 UK time (17:00 ET, after close)
**Purpose:** Learn from the day's biggest movers

```
Using Live Search, review today's US market session. Report:

1. **Top 5 gainers** (%-wise, mid/small cap, >$100M mkt cap):
   | Ticker | % | Catalyst | Did X warn us? |

2. **Top 5 decliners** (same criteria)

3. **Biggest miss** — a stock that moved >20% today where the catalyst
   was actually discoverable on X 24h before. (Teach me what to watch.)

4. **Tomorrow's setup** — 3 stocks that could continue today's momentum
   based on X sentiment and after-hours action.

Under 500 words. Direct, no fluff.
```

---

## How This Complements the Python Tool

| Layer | Source | Frequency | Cost |
|---|---|---:|---:|
| Quant spike radar | Python + yfinance | Every 5 min | Free |
| News aggregator | Finnhub + yfinance + SEC | On-demand | Free tier |
| Social mentions | Reddit + StockTwits | On-demand | Free |
| **Grok Tasks** | **SuperGrok Live Search** | **Scheduled** | **Included in sub** |
| Grok API enrichment | grok-4-fast | On spike alerts | ~$1/mo (optional) |

The Tasks feature handles the scheduled intelligence you'd otherwise pay API
credits for. Keep the Python tool for real-time intraday alerts where Task
scheduling is too slow.

## Tuning Tips

- **Too much noise?** Tighten the prompts: raise thresholds (e.g., "50k+ followers" → "200k+"), or require 2+ signals.
- **Missing moves?** Add specific sectors you follow: "especially biotech, AI, crypto-adjacent".
- **Conflict with Python tool?** Run Tasks 15 min BEFORE the Python radar so you start the day with the narrative context, then the tool picks up live price action.
- **Watchlist too long?** Grok Tasks work best with <15 tickers per prompt. For larger watchlists, split across 2 tasks.

## Integrating Task Outputs with the Python Tool (Optional)

If you want to feed Task outputs back into the tool (e.g., to boost
spike_score for stocks Grok flagged), you can manually paste the output
into `data/grok_task_output.json`. The spike radar can then ingest it —
this integration isn't built yet but is a 30-min job if useful.
