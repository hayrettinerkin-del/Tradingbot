"""
tests/test_cvd_parity.py — Bar-direction vs trade-delta CVD comparison.

Documents expected divergence between proxy and trade-level CVD so
backtest/live gaps can be monitored over time.
"""

import unittest

import numpy as np
import pandas as pd

from cvd_utils import calc_cvd_bar_direction, calc_cvd_trade_delta, cvd_series_diff_pct


class TestCVDParity(unittest.TestCase):

    def _make_bars(self, n: int = 6) -> pd.DataFrame:
        idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
        opens  = [100.0, 99.0, 98.5, 98.0, 99.0, 100.0] * (n // 6 + 1)
        highs  = [101.0, 99.5, 99.0, 98.5, 100.0, 101.0] * (n // 6 + 1)
        lows   = [99.0,  98.0, 98.0, 97.5, 98.5,  99.5]  * (n // 6 + 1)
        closes = [99.5,  98.5, 98.8, 98.2, 99.5, 100.5] * (n // 6 + 1)
        return pd.DataFrame(
            {
                "open":   opens[:n],
                "high":   highs[:n],
                "low":    lows[:n],
                "close":  closes[:n],
                "volume": [1000.0] * n,
            },
            index=idx,
        )

    def test_bar_and_trade_cvd_match_when_side_follows_bar(self):
        """When every trade side matches bar direction, series should align."""
        df = self._make_bars()
        bar_cvd = calc_cvd_bar_direction(df)

        trades = []
        for ts, row in df.iterrows():
            side = "buy" if row["close"] > row["open"] else "sell"
            trades.append({
                "timestamp": int(ts.timestamp() * 1000),
                "amount": row["volume"],
                "side": side,
            })

        trade_cvd = calc_cvd_trade_delta(df.index, trades)
        diff_pct = cvd_series_diff_pct(bar_cvd, trade_cvd)
        self.assertLess(diff_pct, 1.0)

    def test_bar_and_trade_cvd_diverge_on_mixed_flow(self):
        """Heavy selling on up-bars produces measurable proxy vs trade gap."""
        df = self._make_bars()
        bar_cvd = calc_cvd_bar_direction(df)

        # All bars marked up-close in last row but trades are mostly sells
        trades = [
            {"timestamp": int(df.index[2].timestamp() * 1000), "amount": 500, "side": "sell"},
            {"timestamp": int(df.index[3].timestamp() * 1000), "amount": 800, "side": "sell"},
            {"timestamp": int(df.index[4].timestamp() * 1000), "amount": 300, "side": "buy"},
        ]
        trade_cvd = calc_cvd_trade_delta(df.index, trades)

        self.assertNotEqual(
            float(bar_cvd.iloc[-1]),
            float(trade_cvd.iloc[-1]),
        )
        diff_pct = cvd_series_diff_pct(bar_cvd, trade_cvd)
        self.assertGreater(diff_pct, 0.0)

    def test_trade_delta_empty_trades_is_zero(self):
        df = self._make_bars(3)
        trade_cvd = calc_cvd_trade_delta(df.index, [])
        self.assertTrue((trade_cvd == 0).all())


if __name__ == "__main__":
    unittest.main()
