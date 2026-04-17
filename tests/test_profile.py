import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
