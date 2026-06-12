import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import numpy as np
import pandas as pd

from core.engine import WangCaiEngine
from modules.ai_decision import ActionType, TradeDecision
from modules.order_executor import Order, OrderStatus
from modules.risk_control import RiskCheckResult, RiskControlModule
from modules.strategy_factory import normalize_strategy_mode
from modules.yanchi_strategy import YanChiStrategy, SignalType


def make_trend_df(direction: str = "up", n: int = 120) -> pd.DataFrame:
    base = np.linspace(100, 130, n) if direction == "up" else np.linspace(130, 100, n)
    noise = np.sin(np.linspace(0, 8, n)) * 0.4
    close = base + noise
    open_ = close + np.where(direction == "up", -0.2, 0.2)
    high = np.maximum(open_, close) + 0.6
    low = np.minimum(open_, close) - 0.6
    volume = np.linspace(1000, 2200, n)
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


class YanChiStrategyTest(unittest.TestCase):
    def test_aliases_normalize_to_yanchi(self):
        self.assertEqual(normalize_strategy_mode("颜驰Bit"), "yanchi")
        self.assertEqual(normalize_strategy_mode("YanChiBit"), "yanchi")
        self.assertEqual(normalize_strategy_mode(" yanchi "), "yanchi")

    def test_bullish_breakout_returns_buy_with_fixed_size(self):
        df = make_trend_df("up")
        df.loc[df.index[-5:], "high"] += 4
        df.loc[df.index[-3:], "low"] += 2
        df.loc[df.index[-1], "close"] = df["high"].iloc[-2] + 0.5
        df.loc[df.index[-1], "open"] = df.loc[df.index[-1], "close"] - 0.8
        df.loc[df.index[-1], "high"] = df.loc[df.index[-1], "close"] + 0.5
        df.loc[df.index[-1], "low"] = df.loc[df.index[-1], "open"] - 0.5

        signal = YanChiStrategy().generate_signal(df, symbol="NEAR/USDT")

        self.assertEqual(signal.signal, SignalType.BUY)
        self.assertGreaterEqual(signal.score, 4.5)
        self.assertEqual(signal.leverage, 1.0)
        self.assertGreater(signal.position_size, 0)
        self.assertTrue(signal.take_profit_levels)
        rr = (signal.take_profit_levels[0] - signal.price) / (signal.price - signal.stop_price)
        self.assertGreaterEqual(rr, 2.0)

    def test_flat_market_ties_score_and_holds(self):
        df = make_trend_df("up")
        df["close"] = 100.0
        df["open"] = 100.0
        df["high"] = 100.3
        df["low"] = 99.7
        df["volume"] = 1000

        signal = YanChiStrategy().generate_signal(df, symbol="NEAR/USDT")

        self.assertEqual(signal.signal, SignalType.HOLD)
        self.assertIn("平", signal.reason)


class YanChiEngineTest(unittest.TestCase):
    def test_engine_uses_fresh_strategy_instances(self):
        engine = WangCaiEngine.__new__(WangCaiEngine)
        engine.decision_mode = "yanchi"
        engine.market_data = type("MD", (), {"_klines_cache": {}})()
        engine._yanchi_strategy_config = {
            "fixed_position_usdt": 10.0,
            "leverage": 1.0,
            "min_confluence_score": 4.5,
        }

        df = make_trend_df("up")
        engine.market_data._klines_cache["AAA/USDT_1h"] = df
        engine.market_data._klines_cache["BBB/USDT_1h"] = df

        init_calls = []

        class FakeStrategy:
            def __init__(self, config):
                init_calls.append(config.copy())

            def generate_signal(self, df, symbol, df_1h=None, df_4h=None):
                return type("S", (), {
                    "signal": SignalType.BUY,
                    "symbol": symbol,
                    "price": float(df["close"].iloc[-1]),
                    "timestamp": None,
                    "score": 5.0,
                    "reason": "ok",
                    "stop_price": float(df["close"].iloc[-1]) * 0.98,
                    "take_profit_levels": [float(df["close"].iloc[-1]) * 1.05],
                    "leverage": 1.0,
                    "confidence": 0.8,
                    "position_size": 0.1,
                    "risk_percent": 0.02,
                    "confluence_breakdown": {"trend": 1.0},
                })()

        with patch("core.engine.YanChiStrategy", FakeStrategy):
            first = engine._run_yanchi_analysis("AAA/USDT")
            second = engine._run_yanchi_analysis("BBB/USDT")

        self.assertEqual(len(init_calls), 2)
        self.assertEqual(first["symbol"], "AAA/USDT")
        self.assertEqual(second["symbol"], "BBB/USDT")


class RecoverPositionsTest(unittest.TestCase):
    def _make_engine_stub(self, response):
        engine = WangCaiEngine.__new__(WangCaiEngine)
        engine.decision_mode = "yanchi"
        engine.config = {
            "risk": {"stop_loss_pct": 0.01},
            "yanchi": {"min_rr_ratio": 3.0},
            "trinity": {
                "risk": {"max_risk_per_trade": 0.02},
                "take_profit": {"min_rr_ratio": 2.0},
            }
        }

        class DummySession:
            def __init__(self, resp):
                self.resp = resp

            def get(self, url, headers=None, timeout=None):
                return self.resp

        class DummyOrderExecutor:
            def __init__(self, resp):
                self.api_key = "key"
                self.api_secret = "secret"
                self.session = DummySession(resp)
                self.futures_base_url = "https://fapi.binance.com"
                self.positions = []
                self.ensured_orders = []
                self.cleaned_symbols = None

            def _get_symbol_step_size(self, symbol):
                return {"step_size": 0.001, "min_qty": 0.001, "max_qty": 1000000, "min_notional": 5.0}

            def ensure_protection_orders(self, order):
                self.ensured_orders.append(order)
                order.has_exchange_stop = True
                order.has_exchange_tp = True
                return {"has_stop": True, "has_tp": True, "created_stop": False, "created_tp": 0}

            def cleanup_orphan_algo_orders(self, active_symbols):
                self.cleaned_symbols = set(active_symbols)
                return {"checked": 0, "cancelled": 0, "failed": 0, "orphans": [], "errors": []}

        engine.order_executor = DummyOrderExecutor(response)
        return engine

    def test_recover_positions_skips_error_payload(self):
        response = type("Resp", (), {
            "status_code": 400,
            "text": '{"code":-2015,"msg":"Invalid API-key"}',
            "json": lambda self: {"code": -2015, "msg": "Invalid API-key"},
        })()

        engine = self._make_engine_stub(response)
        engine._recover_positions_from_exchange()

        self.assertEqual(engine.order_executor.positions, [])
        self.assertEqual(engine._synced_positions, {})

    def test_recover_positions_accepts_valid_list(self):
        response = type("Resp", (), {
            "status_code": 200,
            "text": "[]",
            "json": lambda self: [
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.01",
                    "entryPrice": "63000",
                    "markPrice": "63500",
                    "leverage": "1",
                }
            ],
        })()

        engine = self._make_engine_stub(response)
        engine._recover_positions_from_exchange()

        self.assertEqual(len(engine.order_executor.positions), 1)
        self.assertEqual(engine._synced_positions["BTCUSDT"].symbol, "BTCUSDT")
        recovered = engine._synced_positions["BTCUSDT"]
        self.assertAlmostEqual(recovered.stop_loss, 62370.0)
        self.assertAlmostEqual(recovered.take_profit_levels[0], 64890.0)
        self.assertTrue(recovered.has_exchange_stop)
        self.assertTrue(recovered.has_exchange_tp)
        self.assertEqual(engine.order_executor.ensured_orders, [recovered])
        self.assertEqual(engine.order_executor.cleaned_symbols, {"BTCUSDT"})

    def test_recover_positions_retries_after_timestamp_error(self):
        class RetrySession:
            def __init__(self):
                self.calls = []
                self.position_calls = 0

            def get(self, url, headers=None, timeout=None):
                self.calls.append(url)
                if url.endswith("/fapi/v1/time"):
                    return type("TimeResp", (), {
                        "status_code": 200,
                        "json": lambda self: {"serverTime": 1_700_000_000_000},
                    })()
                self.position_calls += 1
                if self.position_calls == 1:
                    return type("ErrResp", (), {
                        "status_code": 400,
                        "text": '{"code":-1021,"msg":"Timestamp for this request is outside of the recvWindow."}',
                        "json": lambda self: {"code": -1021, "msg": "Timestamp for this request is outside of the recvWindow."},
                    })()
                return type("OkResp", (), {
                    "status_code": 200,
                    "text": "[]",
                    "json": lambda self: [
                        {
                            "symbol": "ETHUSDT",
                            "positionAmt": "-0.02",
                            "entryPrice": "1680",
                            "markPrice": "1670",
                            "leverage": "1",
                        }
                    ],
                })()

        class RetryOrderExecutor:
            def __init__(self):
                self.api_key = "key"
                self.api_secret = "secret"
                self.session = RetrySession()
                self.futures_base_url = "https://fapi.binance.com"
                self.positions = []

            def _get_symbol_step_size(self, symbol):
                return {"step_size": 0.001, "min_qty": 0.001, "max_qty": 1000000, "min_notional": 5.0}

            def ensure_protection_orders(self, order):
                order.has_exchange_stop = True
                order.has_exchange_tp = True
                return {"has_stop": True, "has_tp": True, "created_stop": False, "created_tp": 0}

            def cleanup_orphan_algo_orders(self, active_symbols):
                return {"checked": 0, "cancelled": 0, "failed": 0, "orphans": [], "errors": []}

        engine = WangCaiEngine.__new__(WangCaiEngine)
        engine.decision_mode = "yanchi"
        engine.config = {
            "risk": {"stop_loss_pct": 0.01},
            "yanchi": {"min_rr_ratio": 3.0},
            "trinity": {
                "risk": {"max_risk_per_trade": 0.02},
                "take_profit": {"min_rr_ratio": 2.0},
            }
        }
        engine.order_executor = RetryOrderExecutor()

        engine._recover_positions_from_exchange()

        self.assertEqual(len(engine.order_executor.positions), 1)
        self.assertEqual(engine._synced_positions["ETHUSDT"].direction, "SHORT")

    def test_recovery_brackets_prefer_persisted_trade_journal_plan(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = f"{tmpdir}/wangcai.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE trade_journal (
                        symbol TEXT,
                        direction TEXT,
                        stop_loss REAL,
                        take_profit_levels TEXT,
                        entry_time TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO trade_journal (
                        symbol, direction, stop_loss, take_profit_levels, entry_time
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "BTCUSDT",
                        "LONG",
                        62000.0,
                        json.dumps([65000.0, 66000.0]),
                        "2026-06-09T20:00:00",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            engine = WangCaiEngine.__new__(WangCaiEngine)
            engine.decision_mode = "yanchi"
            engine.config = {
                "database": {"sqlite_path": db_path},
                "risk": {"stop_loss_pct": 0.01},
                "yanchi": {"min_rr_ratio": 3.0},
            }

            brackets = engine._build_recovery_brackets("BTCUSDT", "LONG", 63000.0)

        self.assertEqual(brackets["stop_loss"], 62000.0)
        self.assertEqual(brackets["take_profit_levels"], [65000.0, 66000.0])

    def test_yanchi_status_reports_prefilter_passed_separately_from_trade_candidates(self):
        engine = WangCaiEngine.__new__(WangCaiEngine)
        engine.cycle_count = 7
        engine._last_screener_result = type("ScreenerResult", (), {
            "passed_count": 49,
            "rejected_count": 2,
            "passed": [],
        })()

        status = engine._build_yanchi_status(
            candidates=[],
            analysis_details=[{"symbol": "BTC/USDT", "status": "analyzed"}],
            original_total=51,
            analyzed_total=49,
            error_count=0,
        )

        self.assertEqual(status["screening"]["total"], 51)
        self.assertEqual(status["screening"]["passed"], 49)
        self.assertEqual(status["screening"]["rejected"], 2)
        self.assertEqual(status["screening"]["trade_candidates"], 0)
        self.assertEqual(status["analysis"]["analyzed"], 49)


class PositionLimitTest(unittest.TestCase):
    def test_engine_cleanup_uses_live_portfolio_symbols(self):
        engine = WangCaiEngine.__new__(WangCaiEngine)
        captured = {}

        class Executor:
            def get_exchange_active_position_symbols(self):
                return set()

            def cleanup_orphan_algo_orders(self, active_symbols):
                captured["symbols"] = set(active_symbols)
                return {"checked": 3, "cancelled": 1, "failed": 0, "orphans": ["AMDUSDT:1"], "errors": []}

        engine.order_executor = Executor()
        pos = type("Pos", (), {"symbol": "FET/USDT:USDT", "amount": 50})()
        account = type("Account", (), {"positions": [pos]})()
        portfolio = type("Portfolio", (), {"accounts": {"binance": account}})()

        result = engine._cleanup_orphan_algo_orders_for_portfolio(portfolio)

        self.assertEqual(captured["symbols"], {"FETUSDT"})
        self.assertEqual(result["cancelled"], 1)

    def test_cleanup_merges_exchange_positions_when_portfolio_sync_is_empty(self):
        engine = WangCaiEngine.__new__(WangCaiEngine)
        captured = {}

        class Executor:
            def get_exchange_active_position_symbols(self):
                return {"GOOGLUSDT", "AMZNUSDT", "SAGAUSDT"}

            def cleanup_orphan_algo_orders(self, active_symbols):
                captured["symbols"] = set(active_symbols)
                return {"checked": 6, "cancelled": 0, "failed": 0, "orphans": [], "errors": []}

        engine.order_executor = Executor()
        portfolio = type("Portfolio", (), {"accounts": {}, "total_equity": 0, "total_positions": 0})()

        result = engine._cleanup_orphan_algo_orders_for_portfolio(portfolio)

        self.assertEqual(captured["symbols"], {"GOOGLUSDT", "AMZNUSDT", "SAGAUSDT"})
        self.assertEqual(result["cancelled"], 0)

    def test_cleanup_skips_when_empty_portfolio_cannot_be_verified(self):
        engine = WangCaiEngine.__new__(WangCaiEngine)

        class Executor:
            def get_exchange_active_position_symbols(self):
                raise RuntimeError("network down")

            def cleanup_orphan_algo_orders(self, active_symbols):
                raise AssertionError("cleanup must be skipped when empty sync is unverified")

        engine.order_executor = Executor()
        portfolio = type("Portfolio", (), {"accounts": {}, "total_equity": 0, "total_positions": 0})()

        result = engine._cleanup_orphan_algo_orders_for_portfolio(portfolio)

        self.assertEqual(result["checked"], 0)
        self.assertIn("unverified_empty_portfolio", result["errors"])

    def test_monitor_skips_invalid_zero_price_snapshot(self):
        engine = WangCaiEngine.__new__(WangCaiEngine)
        close_calls = []

        order = Order(
            order_id="recovered_GOOGLUSDT_1",
            symbol="GOOGLUSDT",
            direction="SHORT",
            status=OrderStatus.OPENED,
            entry_price=360.33,
            entry_time=datetime.now(),
            position_size=0.02,
            leverage=1.0,
            stop_loss=363.93,
            take_profit_levels=[349.52],
            take_profit_quantities=[0.02],
            remaining_quantity=0.02,
            has_exchange_stop=False,
            has_exchange_tp=False,
        )

        class Executor:
            def get_positions(self):
                return [order]

            def close_position(self, *args):
                close_calls.append(args)
                return True

        engine.order_executor = Executor()
        engine.risk_control = type("RC", (), {"record_trade_result": lambda self, pnl: None})()
        snapshot = type("Snap", (), {"avg_price": 0.0})()

        asyncio.run(engine._monitor_positions({"GOOGLUSDT": snapshot}))

        self.assertEqual(close_calls, [])

    def test_risk_control_uses_account_position_count(self):
        risk_control = RiskControlModule({
            "max_positions": 5,
            "capital_per_trade": 10,
            "leverage": 1,
        })

        report = risk_control.check(
            decision={
                "action": "BUY",
                "symbol": "NEAR/USDT",
                "entry_price": 10,
                "stop_loss": 9,
                "take_profit": 12,
            },
            account={
                "total_equity": 100,
                "available_usdt": 100,
                "position_count": 5,
                "existing_symbols": [],
            },
            daily_stats={"total_pnl": 0},
        )

        self.assertFalse(report.is_passed)
        self.assertEqual(report.overall_level, RiskCheckResult.FAIL)
        self.assertTrue(any(item.name == "position_limit" for item in report.checks))

    def test_engine_blocks_opening_when_live_positions_reach_limit(self):
        engine = WangCaiEngine.__new__(WangCaiEngine)
        engine.risk_control = type("RC", (), {"position_sizing": type("PS", (), {"max_positions": 5})()})()
        engine.order_executor = type("OE", (), {"get_positions": lambda self: [object()] * 5})()
        engine.account_manager = type("AM", (), {"get_daily_pnl": lambda self: 0})()
        engine.logger_notifier = type("LN", (), {
            "save_risk_check": lambda self, *args, **kwargs: None,
            "notify_risk": lambda self, *args, **kwargs: None,
        })()
        engine._get_live_open_position_count = WangCaiEngine._get_live_open_position_count.__get__(engine, WangCaiEngine)
        engine._execute_decision = WangCaiEngine._execute_decision.__get__(engine, WangCaiEngine)
        engine.risk_control.check = unittest.mock.Mock()

        decision = TradeDecision(
            action=ActionType.BUY,
            symbol="NEAR/USDT",
            amount=1,
            price=10,
            reason="test",
            confidence=0.9,
        )
        portfolio = type("Portfolio", (), {"total_positions": 3, "total_equity": 100, "total_available": 100})()

        result = asyncio.run(engine._execute_decision(decision, type("Snap", (), {"avg_price": 10})(), portfolio))

        self.assertIsNone(result)
        engine.risk_control.check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
