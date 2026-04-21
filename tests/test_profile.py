import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import profile as profile_module
from bot.profile import load_profile


class ProfileTests(unittest.TestCase):
    def test_paper_profile_sets_small_account_defaults(self):
        original = dict(os.environ)
        try:
            os.environ.pop("BOT_DATA_DIR", None)
            os.environ.pop("BOT_LOGS_DIR", None)
            os.environ.pop("BOT_REPORTS_DIR", None)
            os.environ.pop("RESEARCH_STARTING_EQUITY", None)
            load_profile("paper")
            self.assertEqual(os.environ["ALPACA_PAPER"], "true")
            self.assertEqual(os.environ["SYMBOL"], "SPY")
            self.assertEqual(os.environ["RESEARCH_STARTING_EQUITY"], "250")
            self.assertIn("runtime", os.environ["BOT_DATA_DIR"])
            self.assertIn("paper", os.environ["BOT_DATA_DIR"])
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_live_profile_disables_paper_mode(self):
        original = dict(os.environ)
        try:
            os.environ.pop("BOT_DATA_DIR", None)
            os.environ.pop("BOT_LOGS_DIR", None)
            os.environ.pop("BOT_REPORTS_DIR", None)
            load_profile("live")
            self.assertEqual(os.environ["ALPACA_PAPER"], "false")
            self.assertEqual(os.environ["SYMBOL"], "SPY")
            self.assertIn("live", os.environ["BOT_DATA_DIR"])
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_paper_btc_profile_enables_crypto_defaults(self):
        original = dict(os.environ)
        try:
            os.environ.pop("BOT_DATA_DIR", None)
            os.environ.pop("BOT_LOGS_DIR", None)
            os.environ.pop("BOT_REPORTS_DIR", None)
            load_profile("paper", "btc")
            self.assertEqual(os.environ["ALPACA_PAPER"], "true")
            self.assertEqual(os.environ["SYMBOL"], "BTC/USD")
            self.assertEqual(os.environ["IS_CRYPTO"], "true")
            self.assertEqual(os.environ["ALLOW_OVERNIGHT_HOLDING"], "true")
            self.assertEqual(os.environ["FLATTEN_BEFORE_CLOSE_MINUTES"], "0")
            self.assertIn("paper_btc", os.environ["BOT_DATA_DIR"])
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_hyphenated_profile_name_selects_btc_market(self):
        original = dict(os.environ)
        try:
            os.environ.pop("BOT_DATA_DIR", None)
            os.environ.pop("BOT_LOGS_DIR", None)
            os.environ.pop("BOT_REPORTS_DIR", None)
            load_profile("live-btc")
            self.assertEqual(os.environ["ALPACA_PAPER"], "false")
            self.assertEqual(os.environ["SYMBOL"], "BTC/USD")
            self.assertIn("live_btc", os.environ["BOT_DATA_DIR"])
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_profile_env_overrides_base_env(self):
        original = dict(os.environ)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir(parents=True, exist_ok=True)
            (root / ".env").write_text("RESEARCH_STARTING_EQUITY=100000\nSYMBOL=QQQ\n", encoding="utf-8")
            (root / "config" / "paper_spy.env").write_text(
                "RESEARCH_STARTING_EQUITY=250\nSYMBOL=SPY\n",
                encoding="utf-8",
            )
            try:
                os.environ.pop("BOT_DATA_DIR", None)
                os.environ.pop("BOT_LOGS_DIR", None)
                os.environ.pop("BOT_REPORTS_DIR", None)
                os.environ.pop("RESEARCH_STARTING_EQUITY", None)
                os.environ.pop("SYMBOL", None)
                with patch.object(profile_module, "APP_ROOT", root):
                    load_profile("paper")
                self.assertEqual(os.environ["RESEARCH_STARTING_EQUITY"], "250")
                self.assertEqual(os.environ["SYMBOL"], "SPY")
            finally:
                os.environ.clear()
                os.environ.update(original)

    def test_btc_profile_env_overrides_base_env(self):
        original = dict(os.environ)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir(parents=True, exist_ok=True)
            (root / ".env").write_text("SYMBOL=SPY\nIS_CRYPTO=false\n", encoding="utf-8")
            (root / "config" / "paper_btc.env").write_text(
                "SYMBOL=BTC/USD\nIS_CRYPTO=true\n",
                encoding="utf-8",
            )
            try:
                os.environ.pop("BOT_DATA_DIR", None)
                os.environ.pop("BOT_LOGS_DIR", None)
                os.environ.pop("BOT_REPORTS_DIR", None)
                os.environ.pop("SYMBOL", None)
                os.environ.pop("IS_CRYPTO", None)
                with patch.object(profile_module, "APP_ROOT", root):
                    load_profile("paper", "btc")
                self.assertEqual(os.environ["SYMBOL"], "BTC/USD")
                self.assertEqual(os.environ["IS_CRYPTO"], "true")
            finally:
                os.environ.clear()
                os.environ.update(original)


if __name__ == "__main__":
    unittest.main()
