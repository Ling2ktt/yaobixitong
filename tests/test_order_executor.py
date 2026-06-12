import unittest

from datetime import datetime

from modules.order_executor import Order, OrderExecutorModule, OrderStatus


class ClientOrderIdTest(unittest.TestCase):
    def test_client_order_id_is_truncated_to_binance_limit(self):
        raw_order_id = "WC_CLOSE_recovered_NVDAUSDT_1780928843"

        client_order_id = OrderExecutorModule._make_client_order_id(
            "WC_CLOSE_",
            raw_order_id,
        )

        self.assertLessEqual(len(client_order_id), 36)
        self.assertTrue(client_order_id.startswith("WC_CLOSE_"))


class ExecutorConfigTest(unittest.TestCase):
    def test_sandbox_false_selects_live_futures_endpoint(self):
        executor = OrderExecutorModule({"sandbox": False})

        self.assertFalse(executor.testnet)
        self.assertEqual(executor.futures_base_url, "https://fapi.binance.com")


class ProtectionOrderTest(unittest.TestCase):
    def test_exchange_active_position_symbols_uses_signed_position_amount(self):
        executor = OrderExecutorModule.__new__(OrderExecutorModule)
        calls = []

        def fake_request(method, endpoint, params=None, signed=False, use_futures=False):
            calls.append((method, endpoint, signed, use_futures))
            return [
                {"symbol": "GOOGLUSDT", "positionAmt": "-0.02"},
                {"symbol": "AMZNUSDT", "positionAmt": "0"},
                {"symbol": "SAGAUSDT", "positionAmt": "805.1"},
            ]

        executor._send_request = fake_request

        symbols = executor.get_exchange_active_position_symbols()

        self.assertEqual(symbols, {"GOOGLUSDT", "SAGAUSDT"})
        self.assertEqual(calls, [("GET", "/fapi/v2/positionRisk", True, True)])

    def test_ensure_protection_orders_recreates_missing_stop_and_take_profit(self):
        executor = OrderExecutorModule.__new__(OrderExecutorModule)
        calls = []

        def fake_algo_request(method, endpoint, params=None, signed=False):
            calls.append((method, endpoint, dict(params or {})))
            if method == "GET":
                return []
            return {"algoId": f"algo-{len(calls)}"}

        executor._send_algo_request = fake_algo_request
        executor._get_symbol_step_size = lambda symbol: {
            "step_size": 0.001,
            "min_qty": 0.001,
            "max_qty": 100000,
            "min_notional": 5.0,
        }
        order = Order(
            order_id="recovered_BTCUSDT_1",
            symbol="BTCUSDT",
            direction="LONG",
            status=OrderStatus.OPENED,
            entry_price=100.0,
            entry_time=datetime.now(),
            position_size=0.2,
            leverage=1.0,
            stop_loss=98.0,
            take_profit_levels=[104.0],
            take_profit_quantities=[0.2],
            remaining_quantity=0.2,
        )

        result = executor.ensure_protection_orders(order)

        post_types = [params["type"] for method, _, params in calls if method == "POST"]
        self.assertEqual(post_types, ["STOP_MARKET", "TAKE_PROFIT_MARKET"])
        self.assertTrue(result["has_stop"])
        self.assertTrue(result["has_tp"])
        self.assertTrue(order.has_exchange_stop)
        self.assertTrue(order.has_exchange_tp)

    def test_ensure_protection_orders_recognizes_existing_conditional_order_type(self):
        executor = OrderExecutorModule.__new__(OrderExecutorModule)
        calls = []

        def fake_algo_request(method, endpoint, params=None, signed=False):
            calls.append((method, endpoint, dict(params or {})))
            if method == "GET":
                return [
                    {
                        "symbol": "BTCUSDT",
                        "algoId": 10,
                        "algoType": "CONDITIONAL",
                        "orderType": "STOP_MARKET",
                        "algoStatus": "NEW",
                        "triggerPrice": "98",
                    },
                    {
                        "symbol": "BTCUSDT",
                        "algoId": 11,
                        "algoType": "CONDITIONAL",
                        "orderType": "TAKE_PROFIT_MARKET",
                        "algoStatus": "NEW",
                        "triggerPrice": "104",
                    },
                ]
            return {"algoId": f"algo-{len(calls)}"}

        executor._send_algo_request = fake_algo_request
        executor._get_symbol_step_size = lambda symbol: {
            "step_size": 0.001,
            "min_qty": 0.001,
            "max_qty": 100000,
            "min_notional": 5.0,
        }
        order = Order(
            order_id="recovered_BTCUSDT_1",
            symbol="BTCUSDT",
            direction="LONG",
            status=OrderStatus.OPENED,
            entry_price=100.0,
            entry_time=datetime.now(),
            position_size=0.2,
            leverage=1.0,
            stop_loss=98.0,
            take_profit_levels=[104.0],
            take_profit_quantities=[0.2],
            remaining_quantity=0.2,
        )

        result = executor.ensure_protection_orders(order)

        self.assertEqual([method for method, _, _ in calls], ["GET"])
        self.assertTrue(result["has_stop"])
        self.assertTrue(result["has_tp"])

    def test_tp_split_collapses_when_partial_levels_are_below_min_notional(self):
        levels, qtys = OrderExecutorModule._prepare_tp_slices(
            qty=0.11,
            take_profit_levels=[88.22, 90.0, 92.0],
            ratios=[0.5, 0.3, 0.2],
            step_size=0.01,
            min_qty=0.01,
            min_notional=5.0,
        )

        self.assertEqual(levels, [88.22])
        self.assertEqual(qtys, [0.11])

    def test_cleanup_orphan_algo_orders_cancels_only_symbols_without_positions(self):
        executor = OrderExecutorModule.__new__(OrderExecutorModule)
        calls = []
        open_algos = [
            {"symbol": "FETUSDT", "algoId": 1, "algoStatus": "NEW"},
            {"symbol": "AMDUSDT", "algoId": 2, "algoStatus": "NEW"},
            {"symbol": "NVDAUSDT", "algoId": 3, "algoStatus": "NEW"},
            {"symbol": "ARBUSDT", "algoId": 4, "algoStatus": "CANCELED"},
        ]

        def fake_algo_request(method, endpoint, params=None, signed=False):
            calls.append((method, endpoint, dict(params or {})))
            if method == "GET":
                return open_algos
            return {"success": True}

        executor._send_algo_request = fake_algo_request

        result = executor.cleanup_orphan_algo_orders({"FETUSDT"})

        delete_params = [params for method, _, params in calls if method == "DELETE"]
        self.assertEqual(
            delete_params,
            [
                {"symbol": "AMDUSDT", "algoId": 2},
                {"symbol": "NVDAUSDT", "algoId": 3},
            ],
        )
        self.assertEqual(result["cancelled"], 2)
        self.assertEqual(result["orphans"], ["AMDUSDT:2", "NVDAUSDT:3"])


if __name__ == "__main__":
    unittest.main()
