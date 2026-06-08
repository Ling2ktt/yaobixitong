"""
代币筛选器 + Trinity引擎 - 端到端验证

流程:
  1. 从Gate.io获取10+个代币的行情数据
  2. TokenScreener快速过滤（成交量/趋势/波动）
  3. 只对通过筛选的Top 5代币运行Trinity深度分析
  4. 输出完整报告
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ccxt
import pandas as pd
from datetime import datetime
from modules.token_screener import TokenScreener
from modules.trinity_engine import TrinityEngine


# 测试代币列表（模拟用户添加了大量代币）
TEST_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT",
    "MATIC/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "FIL/USDT",
    "APT/USDT", "ARB/USDT", "OP/USDT", "SUI/USDT", "NEAR/USDT",
]


def main():
    print("=" * 70)
    print("代币筛选 + Trinity分析 - 端到端验证")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()

    exchange = ccxt.gate({'enableRateLimit': True})

    # ================================================================
    # Phase 1: 数据采集（模拟MarketDataModule）
    # ================================================================
    print(">>> Phase 1: 数据采集")
    print(f"    扫描 {len(TEST_SYMBOLS)} 个代币...")
    
    klines_dict = {}
    tickers = {}
    fetch_errors = 0

    for symbol in TEST_SYMBOLS:
        try:
            # 获取1H K线
            ohlcv_1h = exchange.fetch_ohlcv(symbol, '1h', limit=100)
            df_1h = pd.DataFrame(ohlcv_1h, columns=['ts','open','high','low','close','volume'])
            
            # 获取4H K线
            ohlcv_4h = exchange.fetch_ohlcv(symbol, '4h', limit=50)
            df_4h = pd.DataFrame(ohlcv_4h, columns=['ts','open','high','low','close','volume'])
            
            # 获取ticker
            ticker = exchange.fetch_ticker(symbol)
            
            klines_dict[symbol] = {"1h": df_1h, "4h": df_4h}
            tickers[symbol] = {
                'last': ticker.get('last', 0),
                'quoteVolume': ticker.get('quoteVolume', 0),
                'baseVolume': ticker.get('baseVolume', 0),
            }
            
            print(f"    ✅ {symbol:12s} | 价格:${ticker['last']:,.4f} | "
                  f"24h量:${ticker.get('quoteVolume',0):,.0f}")
        except Exception as e:
            print(f"    ❌ {symbol:12s} | {str(e)[:50]}")
            fetch_errors += 1

    print(f"\n    数据采集完成: {len(klines_dict)}/{len(TEST_SYMBOLS)} 代币可用"
          f" ({fetch_errors}个失败)")
    print()

    # ================================================================
    # Phase 2: 代码层预筛选
    # ================================================================
    print("=" * 70)
    print(">>> Phase 2: 代码层预筛选")
    print("=" * 70)

    screener = TokenScreener({
        "min_volume_usdt": 1_000_000,   # 100万USDT最低成交量
        "min_price": 0.001,
        "min_atr_pct": 1.5,
        "max_tokens": 5,                # 最多5个通过
        "trend_check": True,
    })

    result = screener.screen_from_klines(klines_dict, tickers)
    print(screener.generate_report(result))
    print()

    if not result.passed:
        print("❌ 无代币通过筛选，本轮不执行深度分析")
        return

    # ================================================================
    # Phase 3: 只对通过筛选的Top 5代币做Trinity深度分析
    # ================================================================
    print("=" * 70)
    print(f">>> Phase 3: Trinity深度分析 (仅 {len(result.passed)} 个代币)")
    print("=" * 70)
    print(f"    入选代币: {', '.join(result.top_symbols)}")
    print(f"    节省分析: {len(TEST_SYMBOLS) - len(result.passed)} 个代币被过滤掉")
    print()

    engine = TrinityEngine(config={
        'risk_per_trade': 0.02,
        'leverage': 5,
        'min_score_long': 70,
        'min_score_short': 70,
    })

    for token in result.passed:
        symbol = token.symbol
        print(f"--- {symbol} ---")
        
        # 获取完整多时间框架K线
        try:
            df_dict = {}
            for tf in ['4h', '1h', '15m']:
                ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=200)
                df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df_dict[tf] = df
            
            # 日线
            try:
                ohlcv_d = exchange.fetch_ohlcv(symbol, '1d', limit=200)
                df_d = pd.DataFrame(ohlcv_d, columns=['timestamp','open','high','low','close','volume'])
                df_d['timestamp'] = pd.to_datetime(df_d['timestamp'], unit='ms')
                df_dict['daily'] = df_d
            except:
                pass
            
            # 运行Trinity分析
            trinity_signal = engine.analyze(
                df_dict=df_dict, symbol=symbol, account_balance=100.0
            )
            
            # 精简输出
            wy = trinity_signal.wyckoff or {}
            sm = trinity_signal.smc or {}
            pa = trinity_signal.pa or {}
            
            print(f"  筛选评分: {token.total_score:.0f}/100 | "
                  f"Trinity: {trinity_signal.signal} "
                  f"({trinity_signal.grade}级, {trinity_signal.score}/160)")
            print(f"  Wyckoff: {wy.get('phase','?')} | "
                  f"SMC: {sm.get('structure','?')} | "
                  f"PA: {pa.get('always_in','?')} "
                  f"H2={pa.get('h2_ready')} L2={pa.get('l2_ready')}")
            if trinity_signal.signal != 'HOLD':
                entry = trinity_signal.entry.get('price', 'N/A') if trinity_signal.entry else 'N/A'
                print(f"  🚀 信号! 入场:${entry} | SL:${trinity_signal.stop_loss} | "
                      f"仓位:{trinity_signal.position_pct*100:.0f}%")
            print()
            
        except Exception as e:
            print(f"  ❌ Trinity分析失败: {e}")
            print()

    # ================================================================
    # 总结
    # ================================================================
    print("=" * 70)
    print("📊 总结")
    print("=" * 70)
    print(f"  总代币: {len(TEST_SYMBOLS)}")
    print(f"  获取成功: {len(klines_dict)}")
    print(f"  通过筛选: {len(result.passed)}")
    print(f"  被过滤: {result.rejected_count}")
    print()
    print(f"  传统方式: AI分析 {len(TEST_SYMBOLS)} 个代币 = {len(TEST_SYMBOLS)} 次API调用")
    print(f"  筛选后: AI分析 {len(result.passed)} 个代币 = {len(result.passed)} 次调用")
    print(f"  节省: {(1 - len(result.passed)/len(TEST_SYMBOLS))*100:.0f}% AI调用次数")


if __name__ == "__main__":
    main()
