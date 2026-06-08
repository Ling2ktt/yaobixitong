#!/usr/bin/env python3
"""
旺财系统 - 纯本地测试脚本（不依赖外网）
测试策略模块 QuantTrend 的正确性
"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np
from datetime import datetime
from loguru import logger

# 禁用大部分日志，只保留重要信息
logger.remove()
logger.add(sys.stdout, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")


def test_strategy_module():
    """测试规则策略模块（纯本地，无需网络）"""
    print("\n" + "="*60)
    print("  🧪 旺财系统本地测试 - 规则策略模块")
    print("="*60)

    # ── 1. 测试模块导入 ──────────────────────────────────
    print("\n[1/5] 测试模块导入...")
    try:
        from modules.strategy_quant_trend import (
            QuantTrendStrategy, StrategySignal, SignalType
        )
        from modules.market_data import MarketDataModule
        from modules.risk_control import RiskControlModule
        print("  ✅ 所有模块导入成功")
    except Exception as e:
        print(f"  ❌ 模块导入失败: {e}")
        return False

    # ── 2. 生成模拟 K 线数据 ───────────────────────────
    print("\n[2/5] 生成模拟 K 线数据（300根 4H K线）...")
    try:
        np.random.seed(42)
        n = 300
        dates = pd.date_range('2024-01-01', periods=n, freq='4h')

        # 模拟一个上升趋势的价格序列
        trend = np.linspace(50000, 70000, n)
        noise = np.cumsum(np.random.randn(n) * 500)
        prices_close = trend + noise

        df = pd.DataFrame({
            'timestamp': dates,
            'open': prices_close + np.random.randn(n) * 100,
            'high': prices_close + abs(np.random.randn(n) * 200),
            'low': prices_close - abs(np.random.randn(n) * 200),
            'close': prices_close,
            'volume': np.random.randint(100, 5000, n)
        })

        print(f"  ✅ 模拟数据生成成功 | {n} 根K线 | "
              f"价格区间: ${df['close'].min():.0f} ~ ${df['close'].max():.0f}")
    except Exception as e:
        print(f"  ❌ 模拟数据生成失败: {e}")
        return False

    # ── 3. 测试策略信号生成 ─────────────────────────────
    print("\n[3/5] 测试策略信号生成...")
    try:
        strategy = QuantTrendStrategy({
            'fast_len': 18,
            'mid_len': 50,
            'slow_len': 120,
            'min_score': 4.0,   # 降低阈值方便测试
            'leverage': 5.0,
            'hard_stop_perc': 2.0,
            'trail_atr_mult': 2.8,
        })

        signal = strategy.generate_signal(df, symbol="BTC/USDT")
        print(f"  ✅ 信号生成成功")
        print(f"     信号类型: {signal.signal.value}")
        print(f"     趋势评分: {signal.score:.2f}")
        print(f"     杠杆: {signal.leverage}x")
        print(f"     理由: {signal.reason[:80]}...")
    except Exception as e:
        print(f"  ❌ 信号生成失败: {e}")
        import traceback; traceback.print_exc()
        return False

    # ── 4. 测试多次信号生成（模拟主循环）────────
    print("\n[4/5] 测试多次信号生成（模拟20次主循环）...")
    try:
        buy_count = 0
        sell_count = 0
        hold_count = 0

        for i in range(50, len(df), 10):  # 每隔10根K线测试一次
            partial_df = df.iloc[:i].copy()
            sig = strategy.generate_signal(partial_df, symbol="BTC/USDT")
            if sig.signal == SignalType.BUY:
                buy_count += 1
            elif sig.signal == SignalType.SELL:
                sell_count += 1
            else:
                hold_count += 1

        print(f"  ✅ 多次信号测试完成")
        print(f"     BUY 信号: {buy_count} 次")
        print(f"     SELL 信号: {sell_count} 次")
        print(f"     HOLD 信号: {hold_count} 次")
    except Exception as e:
        print(f"  ❌ 多次信号测试失败: {e}")
        import traceback; traceback.print_exc()
        return False

    # ── 5. 测试风控模块 ─────────────────────────────────
    print("\n[5/5] 测试风控模块...")
    try:
        from modules.ai_decision import TradeDecision, ActionType

        risk = RiskControlModule({
            'max_single_order_usdt': 1000,
            'max_daily_loss_usdt': 3000,
            'max_positions': 5,
            'circuit_breaker': {
                'enabled': True,
                'consecutive_losses': 3,
                'cooldown_minutes': 30,
            }
        })

        # 模拟一个买入决策
        decision_dict = {
            'action': 'BUY',
            'symbol': 'BTC/USDT',
            'amount': 0.1,
            'price': float(df['close'].iloc[-1]),
            'reason': '[QuantTrend] 测试买入',
            'confidence': 0.75,
            'stop_loss': float(df['close'].iloc[-1] * 0.98),
            'take_profit': None,
        }

        report = risk.check(
            decision=decision_dict,
            account={
                'total_equity': 10000.0,
                'available_usdt': 5000.0,
                'position_count': 0,
            },
            daily_stats={'total_pnl': 0.0}
        )

        print(f"  ✅ 风控审核完成")
        print(f"     审核结果: {'✅ 通过' if report.is_passed else '❌ 拒绝'}")
        print(f"     风险等级: {report.overall_level.value}")
    except Exception as e:
        print(f"  ❌ 风控测试失败: {e}")
        import traceback; traceback.print_exc()
        return False

    # ── 总结 ─────────────────────────────────────────────
    print("\n" + "="*60)
    print("  🎉 所有测试通过！")
    print("="*60)
    print("\n📝 测试总结：")
    print("  ✅ 模块导入正常")
    print("  ✅ 策略信号生成正常")
    print("  ✅ 多次循环模拟正常")
    print("  ✅ 风控审核正常")
    print("\n💡 下一步：")
    print("  1. 填写 .env 文件（API密钥）")
    print("  2. 运行 python main.py --mode paper 启动模拟交易")
    print("  3. 观察日志，确认信号生成符合预期\n")
    return True


if __name__ == "__main__":
    success = test_strategy_module()
    sys.exit(0 if success else 1)
