"""
tests/test_bot_trend_pullback.py — Unit tests for TrendPullbackBot.
"""

import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

import config
from bot_trend_pullback import TrendPullbackBot, _calc_supertrend_safe


def _make_df(n: int = 250, close_prices=None) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    if close_prices is None:
        close_prices = np.linspace(40_000, 45_000, n)
    close = np.array(close_prices, dtype=float)
    return pd.DataFrame(
        {
            "open":   close * 0.998,
            "high":   close * 1.008,
            "low":    close * 0.992,
            "close":  close,
            "volume": np.random.default_rng(42).uniform(100, 1000, n),
        },
        index=idx,
    )


def _bot_instance():
    with (
        patch("base_bot.config.make_exchange", return_value=MagicMock()),
        patch("base_bot._load_state", return_value={}),
        patch("base_bot._save_state"),
    ):
        bot = TrendPullbackBot.__new__(TrendPullbackBot)
        bot.log            = MagicMock()
        bot.positions      = {}
        bot.closed_trades  = []
        bot.balance        = 400.0
        bot.start_balance  = 400.0
        bot._day_start_balance = 400.0
        bot.paused         = False
        import threading
        bot._positions_lock = threading.Lock()
        bot._trades_lock    = threading.Lock()
        from datetime import date
        bot._current_day    = date.today()
        bot.timeframe       = config.TIMEFRAMES.get("TREND_PULLBACK", "15m")
    return bot


class TestTrendPullbackBot(unittest.TestCase):

    def setUp(self):
        self.bot = _bot_instance()

    def test_insufficient_bars_returns_none(self):
        df = _make_df(50)
        signal = self.bot.generate_signal(df, None)
        self.assertIsNone(signal)

    def test_supertrend_calc_safe(self):
        df = _make_df(100)
        st_val, st_dir = _calc_supertrend_safe(df, 10, 3.0)
        self.assertEqual(len(st_val), 100)
        self.assertEqual(len(st_dir), 100)
        self.assertTrue(all(d in (1.0, -1.0) for d in st_dir.dropna()))

    def test_precompute_indicators_adds_expected_columns(self):
        df = _make_df(220)
        prep = TrendPullbackBot.precompute_indicators(df)
        for col in ["_ema20", "_ema50", "_ema200", "_supert_val", "_supert_dir", "_rsi14", "_prev_rsi14", "_atr14"]:
            self.assertIn(col, prep.columns)

    def test_never_returns_sell_when_flat(self):
        df = _make_df(230)
        signal = self.bot.generate_signal(df, None)
        self.assertIn(signal, [None, "buy"])

    def test_exit_on_supertrend_bearish_flip(self):
        df = _make_df(230)
        prep = TrendPullbackBot.precompute_indicators(df)
        # Force supertrend to bearish on last bar
        prep.loc[prep.index[-1], "_supert_dir"] = -1.0
        pos = {"side": "long", "bars_held": 5, "entry_price": 40000, "peak_price": 40500}
        signal = self.bot.generate_signal(prep, pos)
        self.assertEqual(signal, "close")

    def test_trailing_stop_exit_after_profit(self):
        df = _make_df(230)
        prep = TrendPullbackBot.precompute_indicators(df)
        prep.loc[prep.index[-1], "_supert_dir"] = 1.0
        prep.loc[prep.index[-1], "close"] = 40000.0
        # Position was up > 1.5% to peak 42000, but now at 40000 (pulled back >1.2% from peak)
        pos = {"side": "long", "bars_held": 5, "entry_price": 40000.0, "peak_price": 42000.0}
        signal = self.bot.generate_signal(prep, pos)
        self.assertEqual(signal, "close")

    def test_min_hold_bars_respected(self):
        df = _make_df(230)
        prep = TrendPullbackBot.precompute_indicators(df)
        prep.loc[prep.index[-1], "_supert_dir"] = -1.0
        pos = {"side": "long", "bars_held": 0, "entry_price": 40000, "peak_price": 40000}
        signal = self.bot.generate_signal(prep, pos)
        self.assertIsNone(signal)


if __name__ == "__main__":
    unittest.main()
