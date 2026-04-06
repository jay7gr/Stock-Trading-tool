"""
Configuration for the Automated Trading Tool.
All settings in one place — API keys, targets, risk limits.
"""

# ─── Capital & Targets ───────────────────────────────────────────────
INITIAL_CAPITAL = 20_000.00          # GBP starting capital
DAILY_PROFIT_TARGET_MIN = 200.00     # GBP minimum daily target
DAILY_PROFIT_TARGET_MAX = 500.00     # GBP stretch daily target
DAILY_STOP_LOSS = -200.00            # GBP max daily loss before halting
MONTHLY_TARGET = 600.00              # GBP conservative monthly target
ISA_ANNUAL_LIMIT = 20_000.00         # GBP annual ISA contribution cap

# ─── Position Sizing ─────────────────────────────────────────────────
MAX_POSITION_PCT = 0.15              # Max 15% of portfolio in one stock
MAX_OPEN_POSITIONS = 8               # Max simultaneous positions
MIN_POSITION_SIZE = 200.00           # GBP minimum per trade
DEFAULT_STOP_LOSS_PCT = 0.02         # 2% per-trade stop-loss
DEFAULT_TAKE_PROFIT_PCT = 0.04       # 4% per-trade take-profit (2:1 R/R)

# ─── Trading Schedule (UK time) ──────────────────────────────────────
MARKET_OPEN = "08:00"
MARKET_CLOSE = "16:30"
SCAN_INTERVAL_MINUTES = 15           # How often to scan for signals

# ─── Benchmark ────────────────────────────────────────────────────────
BENCHMARK_TICKER = "VUSA.L"          # Vanguard S&P 500 ETF (LSE)

# ─── Stock Universe ──────────────────────────────────────────────────
# Liquid LSE stocks + ETFs suitable for short-term trading
STOCK_UNIVERSE = [
    # FTSE 100 high-liquidity
    "SHEL.L",   # Shell
    "AZN.L",    # AstraZeneca
    "HSBA.L",   # HSBC
    "ULVR.L",   # Unilever
    "BP.L",     # BP
    "GSK.L",    # GSK
    "RIO.L",    # Rio Tinto
    "LSEG.L",   # London Stock Exchange Group
    "REL.L",    # RELX
    "DGE.L",    # Diageo
    "BA.L",     # BAE Systems
    "LLOY.L",   # Lloyds
    "BARC.L",   # Barclays
    "VOD.L",    # Vodafone
    "NG.L",     # National Grid
    "AAL.L",    # Anglo American
    "ANTO.L",   # Antofagasta
    "MNG.L",    # M&G
    "GLEN.L",   # Glencore
    "BHP.L",    # BHP Group

    # Popular ETFs on LSE
    "VUSA.L",   # Vanguard S&P 500
    "VWRL.L",   # Vanguard FTSE All-World
    "ISF.L",    # iShares Core FTSE 100
    "SWDA.L",   # iShares Core MSCI World
    "VUKE.L",   # Vanguard FTSE 100
]

# Twelve Data uses different symbol format (no .L suffix, exchange specified)
TWELVE_DATA_SYMBOLS = {
    "SHEL.L": "SHEL", "AZN.L": "AZN", "HSBA.L": "HSBA", "ULVR.L": "ULVR",
    "BP.L": "BP.", "GSK.L": "GSK", "RIO.L": "RIO", "LSEG.L": "LSEG",
    "REL.L": "REL", "DGE.L": "DGE", "BA.L": "BA.", "LLOY.L": "LLOY",
    "BARC.L": "BARC", "VOD.L": "VOD", "NG.L": "NG.", "AAL.L": "AAL",
    "ANTO.L": "ANTO", "MNG.L": "MNG", "GLEN.L": "GLEN", "BHP.L": "BHP",
    "VUSA.L": "VUSA", "VWRL.L": "VWRL", "ISF.L": "ISF", "SWDA.L": "SWDA",
    "VUKE.L": "VUKE",
}
TWELVE_DATA_EXCHANGE = "LSE"

# ─── API Keys ────────────────────────────────────────────────────────
# Market data
TWELVE_DATA_API_KEY = None           # Free: twelvedata.com

# AI analysis (Option B: fully automated)
CLAUDE_API_KEY = None                # console.anthropic.com
GROK_API_KEY = None                  # console.x.ai

# Broker
T212_API_KEY = None                  # Trading 212 app → Settings → API
T212_BASE_URL = "https://live.trading212.com/api/v0"

# Notifications
TELEGRAM_BOT_TOKEN = None            # @BotFather on Telegram
TELEGRAM_CHAT_ID = None              # Your chat ID

# ─── AI Model Settings ───────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-20250514"
GROK_MODEL = "grok-3"

# ─── Consensus Settings ──────────────────────────────────────────────
CONSENSUS_MODE = "specialised"       # "agreement", "weighted", "specialised"
CLAUDE_WEIGHT = 0.6
GROK_WEIGHT = 0.4
MIN_CONSENSUS_SCORE = 7.0            # Out of 10 — minimum to execute

# ─── Mode ─────────────────────────────────────────────────────────────
EMULATOR_MODE = True                 # Set False when connected to Trading 212
