import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from core.engine import WangCaiEngine
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


if __name__ == "__main__":
    unittest.main()
