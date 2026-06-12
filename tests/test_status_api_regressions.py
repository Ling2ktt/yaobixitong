import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import web_server
from modules.account_manager import AccountManagerModule
from modules.info_aggregator import InfoAggregatorModule
from modules.logger_notifier import LoggerNotifierModule


class StatusApiRegressionTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmpdir.name) / "wangcai.db"
        self._create_db(self.db_path)
        self.original_db = web_server.DB
        self.original_auth = web_server.AUTH_TOKEN
        web_server.DB = self.db_path
        web_server.AUTH_TOKEN = ""
        self.client = web_server.app.test_client()

    def tearDown(self):
        web_server.DB = self.original_db
        web_server.AUTH_TOKEN = self.original_auth
        self.tmpdir.cleanup()

    def _create_db(self, path: Path):
        con = sqlite3.connect(path)
        cur = con.cursor()
        cur.executescript(
            """
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
            """
        )
        raw = {
            "total_equity_usdt": 99.96,
            "available_usdt": 72.14,
            "position_count": 1,
            "positions": [
                {
                    "symbol": "AMDUSDT",
                    "side": "long",
                    "amount": 0.02,
                    "entry_price": 485.11,
                    "mark_price": 502.14,
                    "unrealized_pnl": 0.34,
                    "leverage": 1,
                }
            ],
        }
        cur.execute(
            """
            INSERT INTO account_snapshots (
                timestamp, exchange, total_equity, available_usdt,
                position_count, daily_pnl, total_pnl, raw_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(),
                "binance",
                99.96,
                72.14,
                1,
                0,
                0,
                json.dumps(raw),
            ),
        )
        con.commit()
        con.close()

    def test_positions_api_falls_back_to_latest_account_snapshot(self):
        resp = self.client.get("/api/positions")
        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.data.decode("utf-8"))

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["symbol"], "AMDUSDT")
        self.assertEqual(payload[0]["side"], "long")

    def test_account_realtime_returns_fresh_cached_snapshot_without_network(self):
        resp = self.client.get("/api/account_realtime")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(payload["source"], "snapshot_cache")
        self.assertEqual(payload["position_count"], 1)
        self.assertEqual(payload["positions"][0]["symbol"], "AMDUSDT")

    def test_account_realtime_skips_newer_empty_failed_snapshot(self):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO account_snapshots (
                    timestamp, exchange, total_equity, available_usdt,
                    position_count, daily_pnl, total_pnl, raw_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(),
                    "binance",
                    0,
                    0,
                    0,
                    0,
                    0,
                    json.dumps({
                        "total_equity_usdt": 0,
                        "available_usdt": 0,
                        "position_count": 0,
                        "positions": [],
                    }),
                ),
            )
            con.commit()
        finally:
            con.close()

        resp = self.client.get("/api/account_realtime")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(payload["source"], "snapshot_cache")
        self.assertEqual(payload["total_equity"], 99.96)
        self.assertEqual(payload["position_count"], 1)
        self.assertEqual(payload["positions"][0]["symbol"], "AMDUSDT")

    def test_positions_api_skips_newer_empty_failed_snapshot(self):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO account_snapshots (
                    timestamp, exchange, total_equity, available_usdt,
                    position_count, daily_pnl, total_pnl, raw_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(),
                    "binance",
                    0,
                    0,
                    0,
                    0,
                    0,
                    json.dumps({
                        "total_equity_usdt": 0,
                        "available_usdt": 0,
                        "position_count": 0,
                        "positions": [],
                    }),
                ),
            )
            con.commit()
        finally:
            con.close()

        resp = self.client.get("/api/positions")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["symbol"], "AMDUSDT")

    def test_account_realtime_returns_stale_valid_snapshot_instead_of_blocking(self):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                "UPDATE account_snapshots SET timestamp=?",
                ("2026-01-01T00:00:00",),
            )
            con.commit()
        finally:
            con.close()

        resp = self.client.get("/api/account_realtime")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(payload["source"], "snapshot_stale")
        self.assertEqual(payload["position_count"], 1)
        self.assertEqual(payload["positions"][0]["symbol"], "AMDUSDT")


class ProcessStatusRegressionTest(unittest.TestCase):
    def test_is_running_does_not_request_process_cwd(self):
        class FakeProcess:
            info = {"cmdline": [str(web_server.ROOT / ".venv" / "Scripts" / "python.exe"), "main.py"]}

        with patch("psutil.process_iter", return_value=[FakeProcess()]) as process_iter:
            self.assertTrue(web_server.is_running())

        attrs = process_iter.call_args.args[0]
        self.assertNotIn("cwd", attrs)


class LoggerNotifierPositionPersistenceTest(unittest.TestCase):
    def test_save_account_snapshot_persists_positions_table(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "wangcai.db"
            notifier = LoggerNotifierModule(
                {
                    "database": {"type": "sqlite", "sqlite_path": str(db_path)},
                    "channels": ["console"],
                }
            )
            notifier.save_account_snapshot(
                {
                    "timestamp": "2026-06-09T20:13:46",
                    "accounts": {
                        "binance": {
                            "total_equity_usdt": 99.96,
                            "available_usdt": 72.14,
                            "position_count": 1,
                            "daily_pnl": 0,
                            "total_pnl": 0,
                            "positions": [
                                {
                                    "symbol": "AMDUSDT",
                                    "side": "long",
                                    "amount": 0.02,
                                    "entry_price": 485.11,
                                    "mark_price": 502.14,
                                    "unrealized_pnl": 0.34,
                                    "leverage": 1,
                                }
                            ],
                        }
                    },
                }
            )

            con = sqlite3.connect(db_path)
            try:
                rows = con.execute("SELECT symbol, status FROM positions").fetchall()
            finally:
                con.close()

        self.assertEqual(rows, [("AMDUSDT", "open")])


class InfoAggregatorTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_session_uses_short_timeout_for_optional_sources(self):
        agg = InfoAggregatorModule({"sources": ["fear_greed"], "request_timeout": 6})
        try:
            session = await agg._get_session()
            self.assertLessEqual(session.timeout.total, 6)
        finally:
            await agg.close()


class AccountManagerSnapshotFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_sync_all_keeps_last_valid_snapshot_when_exchange_fails(self):
        manager = AccountManagerModule({})

        class Exchange:
            def __init__(self):
                self.fail = False

            def fetch_balance(self):
                if self.fail:
                    raise RuntimeError("network down")
                return {
                    "total": {"USDT": 100},
                    "free": {"USDT": 70},
                    "used": {"USDT": 30},
                }

            def fetch_positions(self):
                if self.fail:
                    raise RuntimeError("network down")
                return [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 0.01,
                        "side": "long",
                        "entryPrice": 60000,
                        "markPrice": 61000,
                        "unrealizedPnl": 10,
                    }
                ]

        exchange = Exchange()
        manager.register_exchange("binance", exchange)

        first = await manager.sync_all()
        self.assertEqual(first.total_equity, 100)
        self.assertEqual(first.total_positions, 1)

        exchange.fail = True
        second = await manager.sync_all()

        self.assertIs(second, first)
        self.assertTrue(second.stale)
        self.assertEqual(second.total_equity, 100)
        self.assertEqual(second.total_positions, 1)


if __name__ == "__main__":
    unittest.main()
