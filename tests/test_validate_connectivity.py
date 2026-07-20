import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from bot.validate_connectivity import validate_connectivity


class ValidateConnectivityTests(unittest.TestCase):
    def test_validates_account_and_recent_market_data_without_orders(self):
        trading = Mock()
        trading.get_account.return_value = SimpleNamespace(equity="250.50")
        data = Mock()
        bars = pd.DataFrame(
            {"close": [100.0, 101.0, 102.0]},
            index=pd.date_range("2026-07-20T00:00:00Z", periods=3, freq="5min"),
        )

        with patch.dict(
            os.environ,
            {"SYMBOL": "BTC/USD", "TIMEFRAME_MINUTES": "5", "ALPACA_PAPER": "true"},
            clear=False,
        ), patch("bot.validate_connectivity.make_clients", return_value=(trading, data)), patch(
            "bot.validate_connectivity.get_recent_bars", return_value=bars
        ):
            result = validate_connectivity()

        self.assertTrue(result.paper)
        self.assertEqual(result.bar_count, 3)
        self.assertEqual(result.equity, 250.5)
        trading.submit_order.assert_not_called()

    def test_rejects_empty_market_data(self):
        trading = Mock()
        trading.get_account.return_value = SimpleNamespace(equity="250")

        with patch.dict(os.environ, {"SYMBOL": "BTC/USD"}, clear=False), patch(
            "bot.validate_connectivity.make_clients", return_value=(trading, Mock())
        ), patch("bot.validate_connectivity.get_recent_bars", return_value=pd.DataFrame()):
            with self.assertRaisesRegex(RuntimeError, "market-data connectivity failed"):
                validate_connectivity()


if __name__ == "__main__":
    unittest.main()
