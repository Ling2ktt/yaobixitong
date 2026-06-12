import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import web_server


class TradeJournalApiTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "wangcai.db"
        self._create_db(self.db_path)
        web_server.DB = self.db_path
        web_server.AUTH_TOKEN = ""
        self.client = web_server.app.test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _create_db(self, path: Path):
        con = sqlite3.connect(path)
        cur = con.cursor()
        cur.executescript(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                order_id TEXT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                side TEXT,
                amount REAL,
                price REAL,
                filled_amount REAL,
                average_price REAL,
                fee REAL,
                pnl REAL,
                exchange TEXT,
                status TEXT,
                raw_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                decision_id TEXT,
                action TEXT NOT NULL,
                symbol TEXT,
                amount REAL,
                price REAL,
                confidence REAL,
                reason TEXT,
                strategy TEXT,
                ai_response TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE risk_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                decision_id TEXT,
                overall_level TEXT,
                is_passed INTEGER,
                checks_detail TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                exchange TEXT,
                total_equity REAL,
                available_usdt REAL,
                position_count INTEGER,
                daily_pnl REAL,
                total_pnl REAL,
                raw_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT,
                order_id TEXT,
                decision_id TEXT,
                symbol TEXT,
                direction TEXT,
                status TEXT,
                strategy TEXT,
                entry_time TEXT,
                entry_price REAL,
                stop_loss REAL,
                take_profit_levels TEXT,
                initial_risk_usdt REAL,
                initial_risk_pct REAL,
                fees REAL,
                funding_fee REAL,
                gross_pnl REAL,
                net_pnl REAL,
                r_multiple REAL,
                signal_reason TEXT,
                setup_reason TEXT,
                risk_passed INTEGER,
                risk_level TEXT,
                risk_checks TEXT,
                trend_4h TEXT,
                raw_trade TEXT,
                raw_decision TEXT,
                raw_risk TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            INSERT INTO trades (
                timestamp, order_id, symbol, action, side, amount, price,
                filled_amount, average_price, fee, pnl, exchange, status, raw_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-08T20:35:24.676502",
                "WC_1780922123_3",
                "NVDAUSDT",
                "BUY",
                "buy",
                0.04750368153531899,
                210.51,
                0.04750368153531899,
                210.51,
                0.0,
                0.0,
                "binance",
                "filled",
                "{}",
            ),
        )
        cur.execute(
            """
            INSERT INTO decisions (
                timestamp, decision_id, action, symbol, amount, price,
                confidence, reason, strategy, ai_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-08T20:35:23.315096",
                "",
                "BUY",
                "NVDA/USDT",
                0.04750368153531899,
                210.51,
                0.52,
                "[YanChi] 均线多头排列 | 突破结构高点 | 回踩确认",
                "",
                "",
            ),
        )
        cur.execute(
            """
            INSERT INTO risk_checks (
                timestamp, decision_id, overall_level, is_passed, checks_detail
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-06-08T20:35:23.322098",
                "NVDA/USDT_1780922123",
                "PASS",
                1,
                "[]",
            ),
        )
        cur.execute(
            """
            INSERT INTO trade_journal (
                trade_id, order_id, decision_id, symbol, direction, status,
                strategy, entry_time, entry_price, stop_loss, take_profit_levels,
                initial_risk_usdt, initial_risk_pct, fees, funding_fee,
                gross_pnl, net_pnl, r_multiple, signal_reason, setup_reason,
                risk_passed, risk_level, risk_checks, raw_trade, raw_decision, raw_risk
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "WC_JOURNAL_1",
                "WC_JOURNAL_1",
                "NVDA/USDT_1780922123",
                "NVDAUSDT",
                "LONG",
                "filled",
                "yanchi",
                "2026-06-08T20:35:24.676502",
                210.51,
                205.0,
                json.dumps([221.0, 230.0]),
                0.26,
                0.02,
                0.01,
                0.0,
                0.0,
                -0.01,
                0.0,
                "[YanChi] test",
                "[YanChi] test",
                1,
                "PASS",
                json.dumps([{"rule_name": "risk_reward", "passed": True}]),
                json.dumps({"order_id": "WC_JOURNAL_1"}),
                json.dumps({"stop_loss": 205.0, "take_profit": [221.0, 230.0]}),
                json.dumps({"overall_level": "PASS"}),
            ),
        )
        con.commit()
        con.close()

    def test_trade_journal_api_returns_review_rows(self):
        resp = self.client.get("/api/trade_journal?limit=5&include_events=0&sync_costs=1")
        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.data.decode("utf-8"))
        self.assertIn("trades", payload)
        self.assertEqual(len(payload["trades"]), 1)
        row = payload["trades"][0]
        self.assertEqual(row["symbol"], "NVDAUSDT")
        self.assertEqual(row["direction"], "LONG")
        self.assertEqual(row["strategy"], "yanchi")
        self.assertEqual(row["stop_loss"], 205.0)
        self.assertEqual(row["take_profit_levels"], [221.0, 230.0])
        self.assertEqual(row["risk_checks"], [{"rule_name": "risk_reward", "passed": True}])


if __name__ == "__main__":
    unittest.main()
