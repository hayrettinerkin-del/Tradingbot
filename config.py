"""
Global configuration for the paper trading bot system.
Values can be overridden via a .env file (see .env.example).
"""

import os
from dotenv import load_dotenv

load_dotenv()  # loads .env if present; silently skips if missing

def _float(key: str, default: float) -> float:
    return float(os.getenv(key, default))

def _int(key: str, default: int) -> int:
    return int(os.getenv(key, default))

# ── Safety ────────────────────────────────────────────────────────────────────
PAPER_TRADING = True  # Must always be True; guards against accidental live trading

# ── Capital allocation ────────────────────────────────────────────────────────
INITIAL_BALANCE = _float("INITIAL_BALANCE", 1_000)   # USDT, split across the three bots

BOT_ALLOCATIONS = {
    "TREND_PULLBACK": 0.40,
    "MACD":           0.20,
    "RSI_VWAP":       0.20,
    "CVD":            0.20,
}

# ── Universe ──────────────────────────────────────────────────────────────────
USE_DYNAMIC_UNIVERSE = os.getenv("USE_DYNAMIC_UNIVERSE", "true").lower() in ("1", "true", "yes")
FALLBACK_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
SYMBOLS = FALLBACK_SYMBOLS  # static fallback when dynamic universe is disabled

UNIVERSE_MIN_QUOTE_VOLUME_USDT = _float("UNIVERSE_MIN_QUOTE_VOLUME_USDT", 5_000_000)
UNIVERSE_CANDIDATE_POOL        = _int("UNIVERSE_CANDIDATE_POOL", 50)
UNIVERSE_DAILY_TOP_N           = _int("UNIVERSE_DAILY_TOP_N", 10)   # hacim filtresinden geçen aday havuzu
UNIVERSE_4H_SCAN_TOP           = _int("UNIVERSE_4H_SCAN_TOP", 10)   # skorlanacak coin (whitelist kadar)
UNIVERSE_ACTIVE_COUNT          = _int("UNIVERSE_ACTIVE_COUNT", 10)  # işlem + dashboard listesi (top movers)
UNIVERSE_RESCAN_HOURS          = _int("UNIVERSE_RESCAN_HOURS", 4)
UNIVERSE_SCANNER_TF            = os.getenv("UNIVERSE_SCANNER_TF", "4h")
UNIVERSE_DAILY_TF              = os.getenv("UNIVERSE_DAILY_TF", "1d")
UNIVERSE_W_ATR                 = _float("UNIVERSE_W_ATR", 0.45)
UNIVERSE_W_CHANGE              = _float("UNIVERSE_W_CHANGE", 0.35)
UNIVERSE_W_VOLUME              = _float("UNIVERSE_W_VOLUME", 0.20)
UNIVERSE_EXCLUDE_BASES         = {"USDT", "USDC", "DAI", "EUR", "BUSD"}

# ── Portfolio risk (cross-bot) ────────────────────────────────────────────────
MAX_PORTFOLIO_POSITIONS = _int("MAX_PORTFOLIO_POSITIONS", 4)
MAX_POSITIONS_PER_BOT   = _int("MAX_POSITIONS_PER_BOT", 1)

# ── Trend Pullback strategy parameters ────────────────────────────────────────
SUPERTREND_PERIOD        = _int("SUPERTREND_PERIOD", 10)
SUPERTREND_MULTIPLIER    = _float("SUPERTREND_MULTIPLIER", 3.0)
PULLBACK_RSI_MIN         = _float("PULLBACK_RSI_MIN", 40.0)
PULLBACK_RSI_MAX         = _float("PULLBACK_RSI_MAX", 70.0)
PULLBACK_REQUIRE_UPTREND = os.getenv("PULLBACK_REQUIRE_UPTREND", "true").lower() in ("1", "true", "yes")

# ── MACD entry filters (relaxed defaults) ───────────────────────────────────
MACD_VOL_MULT              = _float("MACD_VOL_MULT", 1.5)
MACD_ADX_MIN               = _float("MACD_ADX_MIN", 18.0)
MACD_RSI_MIN               = _float("MACD_RSI_MIN", 40.0)
MACD_RSI_MAX               = _float("MACD_RSI_MAX", 78.0)
MACD_REQUIRE_HIST_GROWING   = os.getenv("MACD_REQUIRE_HIST_GROWING", "false").lower() in ("1", "true", "yes")
MACD_MIN_ATR_PCT           = _float("MACD_MIN_ATR_PCT", 0.004)  # skip tiny moves vs commission

# ── Risk parameters ───────────────────────────────────────────────────────────
MAX_POSITION_PCT    = _float("MAX_POSITION_PCT",   0.10)  # 10% of bot balance per trade
STOP_LOSS_PCT       = _float("STOP_LOSS_PCT",      0.015) # 1.5%
TAKE_PROFIT_PCT     = _float("TAKE_PROFIT_PCT",    0.030) # 3.0%
MAX_DAILY_LOSS_PCT  = _float("MAX_DAILY_LOSS_PCT", 0.05)  # 5% — bot pauses if breached

# ── Timeframes ────────────────────────────────────────────────────────────────
TIMEFRAMES = {
    "TREND_PULLBACK": "15m",
    "MACD":           "5m",
    "RSI_VWAP":       "1h",
    "CVD":            "15m",
}

# ── Exchange ──────────────────────────────────────────────────────────────────
EXCHANGE_ID     = os.getenv("EXCHANGE_ID", "bybit")
EXCHANGE_OPTS   = {"defaultType": "spot"}  # Spot market (no leverage)
COMMISSION_RATE = _float("COMMISSION_RATE", 0.001)  # 0.1% per trade side (Bybit spot taker)


def make_exchange():
    """Return a configured ccxt exchange instance (spot by default)."""
    import ccxt
    exchange_cls = getattr(ccxt, EXCHANGE_ID)
    ex = exchange_cls(EXCHANGE_OPTS)
    ex.load_markets()
    return ex

# ── Candle history ────────────────────────────────────────────────────────────
OHLCV_LIMIT  = 200   # candles fetched by ccxt for one-off REST calls
WARMUP_BARS  = 250   # candles seeded into each KlineBuffer at startup

# ── Risk guard loop ───────────────────────────────────────────────────────────
SL_TP_CHECK_SECS = _int("SL_TP_CHECK_SECS", 5)  # background SL/TP poll interval

# ── Web dashboard ─────────────────────────────────────────────────────────────
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")  # empty = auth disabled (local dev)

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR        = "logs"
TRADE_LOG_FILE = "logs/trades.csv"
APP_LOG_FILE   = "logs/trading.log"

# ── Terminal dashboard ────────────────────────────────────────────────────────
DASHBOARD_REFRESH_SECS = 10
