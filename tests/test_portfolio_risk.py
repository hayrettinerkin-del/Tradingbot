"""Tests for cross-bot portfolio risk gates."""

import unittest
from unittest.mock import patch

import config
from portfolio_risk import PortfolioRiskManager


def _mock_bot(name: str, positions: dict | None = None):
    bot = type("Bot", (), {})()
    bot.name = name
    bot.positions = positions or {}
    bot._positions_lock = __import__("threading").Lock()
    return bot


class TestPortfolioRisk(unittest.TestCase):

    def test_blocks_when_portfolio_full(self):
        with patch.object(config, "MAX_PORTFOLIO_POSITIONS", 3):
            bots = [
                _mock_bot("A", {"BTC/USDT": {}}),
                _mock_bot("B", {"ETH/USDT": {}}),
                _mock_bot("C", {"SOL/USDT": {}}),
                _mock_bot("D"),
            ]
            mgr = PortfolioRiskManager(lambda: bots)
            ok, reason = mgr.can_open(bots[3], "BNB/USDT")
            self.assertFalse(ok)
            self.assertEqual(reason, "portfolio_max_positions")

    def test_blocks_duplicate_symbol_across_bots(self):
        bots = [_mock_bot("A", {"BTC/USDT": {}}), _mock_bot("B")]
        mgr = PortfolioRiskManager(lambda: bots)
        ok, reason = mgr.can_open(bots[1], "BTC/USDT")
        self.assertFalse(ok)
        self.assertEqual(reason, "symbol_open_other_bot")

    def test_allows_when_under_limits(self):
        bots = [_mock_bot("A"), _mock_bot("B"), _mock_bot("C")]
        mgr = PortfolioRiskManager(lambda: bots)
        ok, reason = mgr.can_open(bots[0], "BTC/USDT")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_blocks_bot_max_positions(self):
        with patch.object(config, "MAX_POSITIONS_PER_BOT", 1):
            bot = _mock_bot("A", {"BTC/USDT": {}})
            mgr = PortfolioRiskManager(lambda: [bot])
            ok, reason = mgr.can_open(bot, "ETH/USDT")
            self.assertFalse(ok)
            self.assertEqual(reason, "bot_max_positions")


if __name__ == "__main__":
    unittest.main()
