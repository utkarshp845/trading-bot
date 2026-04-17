from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
import unittest

import pandas as pd

from bot.broker_alpaca import get_recent_bars


UTC = ZoneInfo("UTC")


class BrokerAlpacaTests(unittest.TestCase):
    def test_get_recent_bars_returns_latest_rows_from_fetched_window(self):
        index = pd.date_range(start=datetime(2026, 4, 7, 13, 30, tzinfo=UTC), periods=300, freq="5min")
        bars = pd.DataFrame(
            {
                "open": range(300),
                "high": range(300),
                "low": range(300),
                "close": range(300),
                "volume": [1000] * 300,
            },
            index=index,
        )

        with patch("bot.broker_alpaca.get_historical_bars", return_value=bars) as mocked:
            recent = get_recent_bars(object(), "SPY", 5, limit=220)

        self.assertEqual(len(recent), 220)
        self.assertEqual(recent.index[0], bars.index[-220])
        self.assertEqual(recent.index[-1], bars.index[-1])
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
