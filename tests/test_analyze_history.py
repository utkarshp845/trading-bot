import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.analyze_history import analyze_database, build_strategy_evidence, render_markdown


def _init_runs_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            ts TEXT,
            symbol TEXT,
            price REAL,
            signal TEXT,
            desired_action TEXT,
            position_qty REAL,
            note TEXT,
            reasons TEXT,
            metrics_json TEXT,
            bar_ts TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            ts TEXT,
            symbol TEXT,
            side TEXT,
            qty REAL,
            status TEXT,
            filled_avg_price REAL,
            filled_qty REAL,
            intent TEXT,
            action_type TEXT,
            notes TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE closed_trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            exit_ts TEXT,
            pnl REAL
        );
        """
    )
    return conn


class AnalyzeHistoryTests(unittest.TestCase):
    def test_empty_database_is_handled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bot.db"
            sqlite3.connect(db_path).close()

            summary = analyze_database(db_path, "empty")

        self.assertTrue(summary["exists"])
        self.assertEqual(summary["run_count"], 0)
        self.assertEqual(summary["real_run_count"], 0)
        self.assertEqual(summary["order_count"], 0)
        self.assertFalse(summary["validation_only"])

    def test_validation_only_database_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bot.db"
            conn = _init_runs_db(db_path)
            conn.execute(
                """
                INSERT INTO runs (ts, symbol, price, signal, desired_action, position_qty, note, reasons, metrics_json, bar_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    "2026-05-21T14:08:20+00:00",
                    "BTC/USD",
                    120.72,
                    "HOLD",
                    "HOLD",
                    0,
                    "runtime_validation_sample",
                    "indicators_not_ready",
                    "{}",
                    "2026-05-21T14:03:20+00:00",
                ),
            )
            conn.commit()
            conn.close()

            summary = analyze_database(db_path, "validation")

        self.assertEqual(summary["run_count"], 1)
        self.assertEqual(summary["validation_run_count"], 1)
        self.assertEqual(summary["real_run_count"], 0)
        self.assertTrue(summary["validation_only"])
        self.assertEqual(summary["validation_symbol_counts"], {"BTC/USD": 1})

    def test_real_history_counts_rejections_orders_and_forward_returns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bot.db"
            conn = _init_runs_db(db_path)
            rows = [
                (
                    "2026-06-01T00:00:00+00:00",
                    "BTC/USD",
                    100.0,
                    "LONG",
                    "HOLD",
                    0,
                    "momentum_filter_failed;trend_up_no_entry",
                    "momentum_filter_failed;trend_up_no_entry",
                )
            ]
            rows.extend(
                (
                    f"2026-06-01T{i // 12:02d}:{(i % 12) * 5:02d}:00+00:00",
                    "BTC/USD",
                    100.0 + i,
                    "HOLD",
                    "HOLD",
                    0,
                    "",
                    "",
                )
                for i in range(1, 13)
            )
            rows.append(
                (
                    "2026-06-01T01:05:00+00:00",
                    "BTC/USD",
                    113.0,
                    "SHORT",
                    "HOLD",
                    0,
                    "short_entry_filters_passed;stale_bar_data",
                    "short_entry_filters_passed;stale_bar_data",
                )
            )
            for idx, row in enumerate(rows, start=1):
                metrics = {
                    "price": row[2],
                    "bar_ts": row[0],
                    "bar_close_ts": row[0],
                    "blocker_reasons": [part for part in row[6].split(";") if part],
                    "decision_reasons": [part for part in row[7].split(";") if part],
                }
                conn.execute(
                    """
                    INSERT INTO runs (id, ts, symbol, price, signal, desired_action, position_qty, note, reasons, metrics_json, bar_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (idx, *row, json.dumps(metrics), row[0]),
                )
            conn.execute(
                """
                INSERT INTO orders (ts, symbol, side, qty, status, filled_avg_price, filled_qty, intent, action_type, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                ("2026-06-01T00:25:00+00:00", "BTC/USD", "buy", 0.001, "FILLED", 103.5, 0.001, "entry", "open_long", "test"),
            )
            conn.execute(
                """
                INSERT INTO closed_trades (symbol, side, exit_ts, pnl)
                VALUES (?, ?, ?, ?);
                """,
                ("BTC/USD", "long", "2026-06-01T00:30:00+00:00", 1.25),
            )
            conn.commit()
            conn.close()

            summary = analyze_database(db_path, "real")

        self.assertEqual(summary["real_run_count"], 14)
        self.assertEqual(summary["order_count"], 1)
        self.assertEqual(summary["filled_order_count"], 1)
        self.assertEqual(summary["stale_data_count"], 1)
        self.assertGreaterEqual(summary["blocked_entry_count"], 1)
        self.assertIn("momentum_filter_failed", summary["rejection_counts"])
        self.assertGreater(summary["near_miss_summary"]["steps"]["+3"]["avg_return"], 0)
        self.assertIn("momentum_filter_failed", summary["near_miss_summary"]["profitable_blocker_counts"])
        self.assertEqual(summary["closed_trade_summary"]["trade_count"], 1)

    def test_build_strategy_evidence_reads_root_and_runtime_databases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            (root / "reports").mkdir()
            (root / "runtime" / "paper_btc" / "data").mkdir(parents=True)
            sqlite3.connect(root / "data" / "bot.db").close()
            sqlite3.connect(root / "runtime" / "paper_btc" / "data" / "bot.db").close()
            (root / "reports" / "research_latest.json").write_text(
                json.dumps(
                    {
                        "symbol": "SPY",
                        "full_summary": {"trade_count": 177, "profit_factor": 0.975},
                        "best_conditions": ["- hold_bucket=60-120m: avg=1.07 trades=23"],
                        "worst_conditions": ["- volume_ratio_bucket=0.8-1.0: avg=-0.38 trades=35"],
                    }
                ),
                encoding="utf-8",
            )

            payload = build_strategy_evidence(root)
            markdown = render_markdown(payload)

        self.assertEqual(len(payload["databases"]), 2)
        self.assertTrue(payload["research"]["exists"])
        self.assertIn("Strategy Evidence Report", markdown)
        self.assertTrue(payload["recommended_experiments"])


if __name__ == "__main__":
    unittest.main()
