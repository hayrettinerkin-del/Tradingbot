"""
Bot 4 — Trend Pullback & SuperTrend Strategy (Spot)
===================================================
Timeframe  : 15-minute candles
Concept    : Trend-Following + Mean-Reversion Pullback Entry
             Trade with the higher-timeframe trend by buying quality dips
             into dynamic support levels (EMA20/EMA50/SuperTrend).

Signal Logic:
  LONG (BUY):
    1. Trend Regime:
       - Price > EMA200 AND EMA50 > EMA200 (Bullish Golden Cross Structure)
       - SuperTrend Direction is Bullish (Green / +1)
    2. Pullback & Rebound Trigger:
       - Price pulled back near/below EMA20 or SuperTrend (or RSI cooled into 40-55)
       - Current bar confirms recovery: Close > EMA20 AND Close >= Open (bullish candle)
       - Momentum turning up: RSI_current >= RSI_previous
       - Not overbought: RSI between 40 and 70
    3. Minimum Volatility: ATR >= 0.3% of price to ensure commission viability

  EXIT (CLOSE):
    1. Trailing Stop  : If position was in profit (>+1.5%) and pulls back >1.2% from peak
    2. Trend Reversal : SuperTrend flips to Bearish (-1) OR 2 consecutive closes below EMA50
    3. Min Hold       : No exit during the first 2 bars
"""

from datetime import datetime, timezone
from typing import Optional, Union

import pandas as pd
import pandas_ta as ta

import config
from base_bot import BaseBot

_MIN_BARS      = 210    # Required for EMA200 + SuperTrend warmup
_MIN_HOLD_BARS = 2      # Don't exit before 2 bars (30 min on 15m TF)
_TRAILING_PCT  = 0.010  # 1.0% pullback from peak triggers trailing close
_PROFIT_TRIGGER_PCT = 0.012 # 1.2% profit required before trailing stop activates

# Precomputed column names
_C_EMA20        = "_ema20"
_C_EMA50        = "_ema50"
_C_EMA200       = "_ema200"
_C_SUPERT_VAL   = "_supert_val"
_C_SUPERT_DIR   = "_supert_dir"
_C_RSI          = "_rsi14"
_C_PREV_RSI     = "_prev_rsi14"
_C_ATR          = "_atr14"
_C_VOL_SMA      = "_vol_sma20"

_TF_SECS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}


def _calc_supertrend_safe(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> tuple[pd.Series, pd.Series]:
    """Calculate SuperTrend and return (value_series, direction_series)."""
    st_df = ta.supertrend(df["high"], df["low"], df["close"], length=period, multiplier=mult)
    if st_df is not None and not st_df.empty:
        dir_col = next((c for c in st_df.columns if c.startswith("SUPERTd_")), None)
        val_col = next((c for c in st_df.columns if c.startswith("SUPERT_")), None)
        if dir_col and val_col:
            return st_df[val_col], st_df[dir_col]

    # Fallback if pandas_ta calculation returned empty
    hl2 = (df["high"] + df["low"]) / 2
    atr = ta.atr(df["high"], df["low"], df["close"], length=period) or (df["high"] - df["low"])
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    direction = pd.Series(1.0, index=df.index)
    st_val = lower.copy()
    return st_val, direction


class TrendPullbackBot(BaseBot):
    name = "TREND_PULLBACK"

    def __init__(self) -> None:
        super().__init__()
        self.timeframe = config.TIMEFRAMES.get("TREND_PULLBACK", "15m")

    # ── Indicator precomputation (called once by BacktestEngine) ──────────────

    @staticmethod
    def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[_C_EMA20]  = ta.ema(df["close"], length=20)
        df[_C_EMA50]  = ta.ema(df["close"], length=50)
        df[_C_EMA200] = ta.ema(df["close"], length=200)

        st_val, st_dir = _calc_supertrend_safe(
            df,
            period=config.SUPERTREND_PERIOD,
            mult=config.SUPERTREND_MULTIPLIER,
        )
        df[_C_SUPERT_VAL] = st_val
        df[_C_SUPERT_DIR] = st_dir

        df[_C_RSI]      = ta.rsi(df["close"], length=14)
        df[_C_PREV_RSI] = df[_C_RSI].shift(1)
        df[_C_ATR]      = ta.atr(df["high"], df["low"], df["close"], length=14)
        df[_C_VOL_SMA]  = df["volume"].rolling(20).mean()
        return df

    # ── Single iteration (live paper trading) ─────────────────────────────────

    def run_once(self) -> None:
        if self.check_daily_loss():
            return

        self.check_stop_loss_take_profit()

        for symbol in self.trading_symbols:
            try:
                self._process_symbol(symbol)
            except Exception as exc:
                self.log.error("Error processing %s: %s", symbol, exc)

    # ── Signal generation (shared by live trading and backtesting) ────────────

    def generate_signal(
        self,
        df: pd.DataFrame,
        position: Union[None, str, dict] = None,
    ) -> Optional[str]:
        if len(df) < _MIN_BARS:
            return None

        # ── Unpack position context ───────────────────────────────────────────
        if isinstance(position, dict):
            pos_side    = position.get("side")
            bars_held   = int(position.get("bars_held", 0))
            peak_price  = float(position.get("peak_price") or 0.0)
            entry_price = float(position.get("entry_price") or 0.0)
        else:
            pos_side    = position
            bars_held   = 0
            peak_price  = 0.0
            entry_price = 0.0

        # ── Fast path: precomputed columns present ────────────────────────────
        if _C_EMA200 in df.columns:
            ema20      = float(df[_C_EMA20].iloc[-1])
            ema50      = float(df[_C_EMA50].iloc[-1])
            ema200     = float(df[_C_EMA200].iloc[-1])
            prev_ema50 = float(df[_C_EMA50].iloc[-2])
            st_val     = float(df[_C_SUPERT_VAL].iloc[-1])
            st_dir     = float(df[_C_SUPERT_DIR].iloc[-1])
            rsi        = float(df[_C_RSI].iloc[-1])
            prev_rsi   = float(df[_C_PREV_RSI].iloc[-1])
            atr        = float(df[_C_ATR].iloc[-1])
            vol_sma    = float(df[_C_VOL_SMA].iloc[-1]) if _C_VOL_SMA in df.columns else 0.0
            vol_now    = float(df["volume"].iloc[-1])
            price      = float(df["close"].iloc[-1])
            prev_close = float(df["close"].iloc[-2])
            bar_open   = float(df["open"].iloc[-1])
            low_now    = float(df["low"].iloc[-1])
            low_prev   = float(df["low"].iloc[-2])

            if any(pd.isna(v) for v in [ema20, ema50, ema200, st_val, st_dir, rsi, prev_rsi, atr]):
                return None

        # ── Slow path: compute from scratch (live trading fallback) ──────────
        else:
            ema20_s  = ta.ema(df["close"], length=20)
            ema50_s  = ta.ema(df["close"], length=50)
            ema200_s = ta.ema(df["close"], length=200)
            rsi_s    = ta.rsi(df["close"], length=14)
            atr_s    = ta.atr(df["high"], df["low"], df["close"], length=14)
            st_val_s, st_dir_s = _calc_supertrend_safe(
                df, period=config.SUPERTREND_PERIOD, mult=config.SUPERTREND_MULTIPLIER
            )

            if any(s is None or s.empty for s in [ema20_s, ema50_s, ema200_s, rsi_s, atr_s, st_val_s]):
                return None

            ema20      = float(ema20_s.iloc[-1])
            ema50      = float(ema50_s.iloc[-1])
            ema200     = float(ema200_s.iloc[-1])
            prev_ema50 = float(ema50_s.iloc[-2])
            st_val     = float(st_val_s.iloc[-1])
            st_dir     = float(st_dir_s.iloc[-1])
            rsi        = float(rsi_s.iloc[-1])
            prev_rsi   = float(rsi_s.iloc[-2])
            atr        = float(atr_s.iloc[-1])
            vol_sma    = float(df["volume"].rolling(20).mean().iloc[-1])
            vol_now    = float(df["volume"].iloc[-1])
            price      = float(df["close"].iloc[-1])
            prev_close = float(df["close"].iloc[-2])
            bar_open   = float(df["open"].iloc[-1])
            low_now    = float(df["low"].iloc[-1])
            low_prev   = float(df["low"].iloc[-2])

        # ── Position Exit Logic ───────────────────────────────────────────────
        if pos_side == "long":
            if bars_held < _MIN_HOLD_BARS:
                return None

            # Trailing stop: lock in profit if we achieved >1.2% and pull back >1.0% from peak
            if entry_price > 0 and peak_price >= entry_price * (1 + _PROFIT_TRIGGER_PCT):
                if price < peak_price * (1 - _TRAILING_PCT):
                    return "close"

            # Trend reversal exit: SuperTrend flipped to bearish (-1)
            if st_dir < 0:
                return "close"

            # Structural breakdown: 2 consecutive bars closing below EMA50
            if price < ema50 and prev_close < prev_ema50:
                return "close"

        # ── Flat Entry Logic (Buy / Long) ─────────────────────────────────────
        else:
            # 1. Macro Trend Filter: Bullish structure
            is_uptrend = price > ema200 and ema50 > ema200 and ema20 >= ema50 if config.PULLBACK_REQUIRE_UPTREND else price > ema200
            supertrend_bullish = st_dir > 0  # Green SuperTrend

            if not (is_uptrend and supertrend_bullish):
                return None

            # 2. Minimum Volatility & Non-zero volume
            atr_pct = atr / price if price > 0 else 0.0
            if atr_pct < 0.003:  # skip dead periods
                return None
            if vol_sma > 0 and vol_now < vol_sma * 0.5:  # skip extremely low volume anomalies
                return None

            # 3. Pullback Detection:
            # Did price touch/dip near EMA20 or SuperTrend recently, or did RSI cool down?
            touched_ema20   = (low_now <= ema20 * 1.002) or (low_prev <= ema20 * 1.002)
            rsi_cooled_down = prev_rsi <= 55.0
            pullback_occurred = touched_ema20 or rsi_cooled_down

            # 4. Rebound & Trigger:
            # Bar must close bullish (green bar and above EMA20), RSI recovering and not overbought
            bullish_close = price > ema20 and price >= bar_open
            rsi_recovering = rsi >= prev_rsi
            rsi_valid = config.PULLBACK_RSI_MIN <= rsi <= config.PULLBACK_RSI_MAX

            if pullback_occurred and bullish_close and rsi_recovering and rsi_valid:
                return "buy"

        return None

    # ── Per-symbol live logic ─────────────────────────────────────────────────

    def _process_symbol(self, symbol: str) -> None:
        if symbol not in self._buffers:
            self.log.debug("Buffer not yet attached for %s — skipping.", symbol)
            return
        df = self._buffers[symbol].get_df()
        n  = len(df)
        if n < _MIN_BARS:
            self.log.debug("TREND_PULLBACK %s | bars=%d/%d — warming up", symbol, n, _MIN_BARS)
            return

        pos_context: Optional[dict] = None

        if symbol in self.positions:
            pos   = self.positions[symbol]
            price = float(df["close"].iloc[-1])

            if "peak_price" not in pos:
                pos["peak_price"] = pos["entry_price"]
            pos["peak_price"] = max(pos["peak_price"], price)

            tf_secs   = _TF_SECS.get(self.timeframe, 900)
            elapsed   = (datetime.now(timezone.utc) - pos["opened_at"]).total_seconds()
            bars_held = max(0, int(elapsed / tf_secs))

            pos_context = {
                "side":        pos["side"],
                "bars_held":   bars_held,
                "peak_price":  pos["peak_price"],
                "entry_price": pos["entry_price"],
            }

        signal = self.generate_signal(df, pos_context)

        # Indicator debug log
        try:
            _pnow = float(df["close"].iloc[-1])
            _e20  = float(ta.ema(df["close"], length=20).iloc[-1])
            _e50  = float(ta.ema(df["close"], length=50).iloc[-1])
            _e200 = float(ta.ema(df["close"], length=200).iloc[-1])
            _rsi  = float(ta.rsi(df["close"], length=14).iloc[-1])
            _st_v, _st_d = _calc_supertrend_safe(df, config.SUPERTREND_PERIOD, config.SUPERTREND_MULTIPLIER)
            _st_dir = float(_st_d.iloc[-1])
            self.log.debug(
                "TREND_PULLBACK %s | price=%.2f ema20=%.2f ema50=%.2f ema200=%.2f "
                "rsi=%.1f st_dir=%.0f pos=%s → %s",
                symbol, _pnow, _e20, _e50, _e200, _rsi, _st_dir,
                "long" if pos_context else "flat",
                signal or "no_signal",
            )
        except Exception:
            pass

        if signal == "buy" and pos_context is None:
            self.open_position(symbol, "long")
        elif signal == "close" and pos_context is not None:
            self.close_position(symbol, reason="trend_pullback_exit")
