"""
tests/test_base_bot.py — Unit tests for BaseBot mechanics.

Tests cover: position open/close, commission, daily-loss guard,
daily-trade limit, SL/TP, and thread safety.
"""

import threading
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import config


# ── Fixture ───────────────────────────────────────────────────────────────────

def _make_bot(balance: float = 3_300.0):
    """Return a BaseBot subclass instance with all I/O mocked out."""
    from base_bot import BaseBot

    class ConcreteBot(BaseBot):
        name = "TEST"
        def run_once(self): pass
        def _process_symbol(self, symbol): pass

    mock_exchange = MagicMock()
    with (
        patch("base_bot.config.make_exchange", return_value=mock_exchange),
        patch("base_bot._load_state", return_value={}),
        patch("base_bot._save_state"),
    ):
        bot = ConcreteBot()
    bot.exchange = mock_exchange
    bot.balance = balance
    bot.start_balance = balance
    bot._day_start_balance = balance
    return bot


# ── Open / Close ──────────────────────────────────────────────────────────────

class TestPositionLifecycle(unittest.TestCase):

    def setUp(self):
        self.bot = _make_bot()
        self.bot.exchange.fetch_ticker = MagicMock(return_value={"last": 40_000.0})

    def test_open_creates_position(self):
        self.bot.open_position("BTC/USDT", "long")
        self.assertIn("BTC/USDT", self.bot.positions)

    def test_open_deducts_commission(self):
        before = self.bot.balance
        self.bot.open_position("BTC/USDT", "long")
        notional = before * config.MAX_POSITION_PCT
        expected_comm = notional * config.COMMISSION_RATE
        self.assertAlmostEqual(self.bot.balance, before - expected_comm, places=4)

    def test_open_same_symbol_twice_is_noop(self):
        self.bot.open_position("BTC/USDT", "long")
        balance_after_first = self.bot.balance
        self.bot.open_position("BTC/USDT", "long")
        self.assertEqual(self.bot.balance, balance_after_first)
        self.assertEqual(len(self.bot.positions), 1)

    def test_close_removes_position(self):
        self.bot.open_position("BTC/USDT", "long")
        self.bot.close_position("BTC/USDT")
        self.assertNotIn("BTC/USDT", self.bot.positions)

    def test_close_nonexistent_returns_none(self):
        result = self.bot.close_position("ETH/USDT")
        self.assertIsNone(result)

    def test_close_records_trade(self):
        self.bot.open_position("BTC/USDT", "long")
        self.bot.close_position("BTC/USDT", reason="test")
        self.assertEqual(len(self.bot.closed_trades), 1)
        trade = self.bot.closed_trades[0]
        self.assertEqual(trade["reason"], "test")
        self.assertIn("commission", trade)
        self.assertIn("net_pnl", trade)

    def test_profitable_close_increases_balance(self):
        self.bot.open_position("BTC/USDT", "long")
        # Simulate price increase to 44 000
        self.bot.exchange.fetch_ticker = MagicMock(return_value={"last": 44_000.0})
        pnl = self.bot.close_position("BTC/USDT")
        self.assertGreater(pnl, 0)

    def test_loss_close_decreases_balance(self):
        self.bot.open_position("BTC/USDT", "long")
        # Simulate price drop to 36 000
        self.bot.exchange.fetch_ticker = MagicMock(return_value={"last": 36_000.0})
        pnl = self.bot.close_position("BTC/USDT")
        self.assertLess(pnl, 0)

    def test_net_pnl_equals_gross_minus_commission(self):
        self.bot.open_position("BTC/USDT", "long")
        self.bot.exchange.fetch_ticker = MagicMock(return_value={"last": 42_000.0})
        self.bot.close_position("BTC/USDT")
        t = self.bot.closed_trades[0]
        self.assertAlmostEqual(t["net_pnl"], t["gross_pnl"] - t["commission"], places=4)


# ── Daily loss guard ──────────────────────────────────────────────────────────

class TestDailyLossGuard(unittest.TestCase):

    def setUp(self):
        self.bot = _make_bot(balance=1_000.0)
        self.bot._day_start_balance = 1_000.0

    def test_not_paused_within_limit(self):
        self.bot.balance = 960.0  # -4 % loss, limit is -5 %
        paused = self.bot.check_daily_loss()
        self.assertFalse(paused)

    def test_paused_when_limit_breached(self):
        self.bot.balance = 940.0  # -6 % loss
        paused = self.bot.check_daily_loss()
        self.assertTrue(paused)
        self.assertTrue(self.bot.paused)

    def test_resets_on_new_day(self):
        self.bot.balance = 900.0
        self.bot.check_daily_loss()
        self.assertTrue(self.bot.paused)

        from datetime import timedelta
        self.bot._current_day = self.bot._utc_today() - timedelta(days=1)
        self.bot.check_daily_loss()
        self.assertFalse(self.bot.paused)
        self.assertEqual(self.bot._day_start_balance, self.bot.balance)


# ── SL / TP ───────────────────────────────────────────────────────────────────

class TestStopLossTakeProfit(unittest.TestCase):

    def setUp(self):
        self.bot = _make_bot()
        self.bot.exchange.fetch_ticker = MagicMock(return_value={"last": 40_000.0})

    def _open_and_set_price(self, new_price: float) -> None:
        self.bot.open_position("BTC/USDT", "long")
        self.bot.exchange.fetch_ticker = MagicMock(return_value={"last": new_price})

    def test_stop_loss_closes_position(self):
        entry = 40_000.0
        sl_price = entry * (1 - config.STOP_LOSS_PCT - 0.001)  # just below SL
        self._open_and_set_price(sl_price)
        self.bot.check_stop_loss_take_profit()
        self.assertNotIn("BTC/USDT", self.bot.positions)
        self.assertEqual(self.bot.closed_trades[-1]["reason"], "stop_loss")

    def test_take_profit_closes_position(self):
        entry = 40_000.0
        tp_price = entry * (1 + config.TAKE_PROFIT_PCT + 0.001)  # just above TP
        self._open_and_set_price(tp_price)
        self.bot.check_stop_loss_take_profit()
        self.assertNotIn("BTC/USDT", self.bot.positions)
        self.assertEqual(self.bot.closed_trades[-1]["reason"], "take_profit")

    def test_no_close_within_sl_tp_range(self):
        entry = 40_000.0
        safe_price = entry * 1.01  # +1%, between SL and TP
        self._open_and_set_price(safe_price)
        self.bot.check_stop_loss_take_profit()
        self.assertIn("BTC/USDT", self.bot.positions)

    def test_sl_tp_runs_when_paused_on_candle_close(self):
        """Daily-loss pause must not block SL/TP on candle close."""
        from kline_buffer import KlineBuffer
        import pandas as pd
        import numpy as np

        self.bot.paused = True
        buf = KlineBuffer(maxlen=50)
        idx = pd.date_range("2024-01-01", periods=5, freq="5min", tz="UTC")
        df = pd.DataFrame(
            {
                "open":   [40_000.0] * 5,
                "high":   [40_000.0] * 5,
                "low":    [38_000.0] * 5,
                "close":  [39_000.0] * 5,
                "volume": [100.0] * 5,
            },
            index=idx,
        )
        buf.seed(df)
        self.bot.attach_buffer("BTC/USDT", buf)
        self.bot.open_position("BTC/USDT", "long")
        with patch.object(self.bot, "_process_symbol") as mock_proc:
            self.bot.on_candle_close("BTC/USDT")
        mock_proc.assert_not_called()
        self.assertNotIn("BTC/USDT", self.bot.positions)


class TestStats(unittest.TestCase):

    def setUp(self):
        self.bot = _make_bot()
        self.bot.exchange.fetch_ticker = MagicMock(return_value={"last": 40_000.0})

    def test_get_stats_includes_daily_and_equity(self):
        self.bot.open_position("BTC/USDT", "long")
        self.bot.exchange.fetch_ticker = MagicMock(return_value={"last": 41_000.0})
        s = self.bot.get_stats()
        self.assertIn("daily_pnl", s)
        self.assertIn("equity", s)
        self.assertIn("unrealized_pnl", s)
        self.assertGreater(s["unrealized_pnl"], 0)


# ── Thread safety ─────────────────────────────────────────────────────────────

class TestThreadSafety(unittest.TestCase):

    def test_concurrent_open_close_no_corruption(self):
        """Multiple threads opening/closing should not corrupt positions or trades."""
        bot = _make_bot(balance=100_000.0)
        bot.exchange.fetch_ticker = MagicMock(return_value={"last": 100.0})

        errors = []

        def worker(symbol):
            try:
                bot.open_position(symbol, "long")
                bot.close_position(symbol)
            except Exception as exc:
                errors.append(exc)

        symbols = [f"COIN{i}/USDT" for i in range(20)]
        threads = [threading.Thread(target=worker, args=(s,)) for s in symbols]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread errors: {errors}")
        self.assertEqual(len(bot.positions), 0)


if __name__ == "__main__":
    unittest.main()
