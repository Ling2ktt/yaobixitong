"""
三位一体策略 - 快速测试脚本

测试三层分析器的基本功能
"""

import sys
import os
import pandas as pd
import numpy as np

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.trinity_wyckoff import WyckoffAnalyzer
from modules.trinity_smc import SMCAnalyzer
from modules.trinity_pa import PAAnalyzer
from modules.trinity_engine import TrinityEngine


def generate_test_data(n: int = 200, trend: str = "bull", include_climax: bool = True):
    """生成模拟K线数据"""
    np.random.seed(42)
    
    prices = []
    price = 100.0
    
    for i in range(n):
        if trend == "bull":
            # 牛市: 缓慢上涨+回调
            if i > 50 and i % 20 < 5 and include_climax:  # 回调期
                change = np.random.normal(-0.3, 0.5)
            elif i > 100 and i < 105 and include_climax:  # 高潮区 (Spring)
                change = np.random.normal(-1.5, 0.8)
                if change > -1:
                    change = np.random.normal(-1.0, 0.5)
            else:
                change = np.random.normal(0.3, 0.5)
        elif trend == "bear":
            if i > 50 and i % 20 < 5 and include_climax:
                change = np.random.normal(0.3, 0.5)
            elif i > 100 and i < 105 and include_climax:
                change = np.random.normal(1.5, 0.8)
                if change < 1:
                    change = np.random.normal(1.0, 0.5)
            else:
                change = np.random.normal(-0.3, 0.5)
        else:  # range
            if price > 110:
                change = np.random.normal(-0.3, 0.3)
            elif price < 90:
                change = np.random.normal(0.3, 0.3)
            else:
                change = np.random.normal(0, 0.5)
        
        price += change
        price = max(price, 1.0)
        prices.append(price)
    
    # 生成OHLCV
    df = pd.DataFrame()
    df['close'] = prices
    
    ranges = np.abs(np.diff(prices, prepend=prices[0])) * 2 + np.random.uniform(0.3, 1.0, n)
    df['high'] = df['close'] + ranges * np.random.uniform(0.3, 0.7, n)
    df['low'] = df['close'] - ranges * np.random.uniform(0.3, 0.7, n)
    df['open'] = df['close'].shift(1)
    df['open'].iloc[0] = df['close'].iloc[0] - ranges[0] * 0.2
    
    # 确保high/close/low合理
    df['high'] = df[['high', 'open', 'close']].max(axis=1)
    df['low'] = df[['low', 'open', 'close']].min(axis=1)
    
    df['volume'] = np.random.uniform(100, 500, n) * (1 + abs(df['close'].diff()) * 0.5)
    
    return df


def test_wyckoff():
    """测试威科夫层"""
    print("=" * 60)
    print("测试 Wyckoff 结构层")
    print("=" * 60)
    
    # 测试牛市(含Spring)数据
    df_bull = generate_test_data(200, "bull", include_climax=True)
    analyzer = WyckoffAnalyzer()
    signal = analyzer.analyze(df_bull)
    
    print(f"牛市数据 → 阶段: {signal.phase}, 偏向: {signal.bias}")
    print(f"  置信: {signal.confidence}, Spring: {signal.spring_detected}")
    print(f"  SOS: {signal.sos_confirmed}, 趋势: {signal.trend_structure}")
    print(f"  区间: {signal.range_low:.2f} - {signal.range_high:.2f}")
    print()
    
    # 测试熊市
    df_bear = generate_test_data(200, "bear", include_climax=True)
    signal2 = analyzer.analyze(df_bear)
    
    print(f"熊市数据 → 阶段: {signal2.phase}, 偏向: {signal2.bias}")
    print(f"  置信: {signal2.confidence}, UTAD: {signal2.utad_detected}")
    print(f"  SOW: {signal2.sow_confirmed}, 趋势: {signal2.trend_structure}")
    print()
    
    # 测试区间
    df_range = generate_test_data(200, "range", include_climax=False)
    signal3 = analyzer.analyze(df_range)
    
    print(f"区间数据 → 阶段: {signal3.phase}, 偏向: {signal3.bias}")
    print(f"  置信: {signal3.confidence}")
    print()


def test_smc():
    """测试SMC层"""
    print("=" * 60)
    print("测试 SMC 机构层")
    print("=" * 60)
    
    df = generate_test_data(200, "bull", include_climax=True)
    analyzer = SMCAnalyzer()
    signal = analyzer.analyze(df)
    
    print(f"结构: {signal.structure}")
    print(f"  BOS次数: {signal.bos_count}")
    print(f"  CHoCH: {signal.choch}, MSS: {signal.mss}")
    print(f"  流动性猎杀: {signal.liquidity_sweep}")
    print(f"  OB类型: {signal.order_block.get('type')}")
    print(f"  OB近端: {signal.order_block.get('proximal')}")
    print(f"  OB远端: {signal.order_block.get('distal')}")
    print(f"  OB质量: {signal.order_block.get('quality')}")
    print(f"  FVG: {signal.fvg}")
    print(f"  OTE: {signal.ote_zone}")
    print(f"  POI数量: {len(signal.poi_list)}")
    print()


def test_pa():
    """测试PA层"""
    print("=" * 60)
    print("测试 PA 执行层")
    print("=" * 60)
    
    df = generate_test_data(200, "bull", include_climax=True)
    analyzer = PAAnalyzer()
    signal = analyzer.analyze(df, wyckoff_bias="BULL")
    
    print(f"始终在场: {signal.always_in}")
    print(f"  趋势强度: {signal.trend_strength}/5")
    print(f"  回调腿数: {signal.callback_legs}")
    print(f"  H2就绪: {signal.h2_ready}, L2就绪: {signal.l2_ready}")
    print(f"  信号K线质量: {signal.signal_bar_quality}")
    print(f"  信号K线类型: {signal.signal_bar_type}")
    print(f"  EMA位置: {signal.ema_position}")
    print(f"  铁丝网: {signal.is_barbwire}")
    print(f"  高潮警告: {signal.climax_warning}")
    print(f"  测量移动目标: {signal.measured_move_target}")
    print(f"  建议入场: {signal.entry_price}")
    print(f"  建议止损: {signal.stop_loss}")
    print()


def test_full_engine():
    """测试完整引擎"""
    print("=" * 60)
    print("测试 三位一体完整引擎")
    print("=" * 60)
    
    # 生成多时间框架数据
    df_daily = generate_test_data(200, "bull", include_climax=True)
    df_4h = generate_test_data(200, "bull", include_climax=True)
    df_1h = generate_test_data(200, "bull", include_climax=True)
    df_15m = generate_test_data(200, "bull", include_climax=True)
    
    df_dict = {
        "daily": df_daily,
        "4h": df_4h,
        "1h": df_1h,
        "15m": df_15m,
    }
    
    engine = TrinityEngine(config={
        "risk_per_trade": 0.02,
        "leverage": 5,
        "max_positions": 2,
        "max_daily_loss": 0.06,
    })
    
    signal = engine.analyze(
        df_dict=df_dict,
        symbol="BTCUSDT",
        account_balance=100.0
    )
    
    # 打印报告
    print(engine.get_status_report(signal))
    print()
    
    # 打印决策路径
    print("--- 决策路径 ---")
    for line in signal.decision_path:
        print(line)


if __name__ == "__main__":
    test_wyckoff()
    test_smc()
    test_pa()
    test_full_engine()
