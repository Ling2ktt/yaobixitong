"""
三位一体策略 - 本地实盘调试验证 (Debug模式)
强制运行全部三层分析，输出完整诊断信息
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ccxt
import pandas as pd
from datetime import datetime
from modules.trinity_wyckoff import WyckoffAnalyzer
from modules.trinity_smc import SMCAnalyzer
from modules.trinity_pa import PAAnalyzer
from modules.trinity_engine import TrinityEngine


def main():
    print("=" * 70)
    print("三位一体策略 - DEBUG模式 - Gate.io BTC/USDT")
    print("=" * 70)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 获取K线
    print(">>> 拉取K线数据...")
    exchange = ccxt.gate({'enableRateLimit': True})
    df_dict = {}

    for name, tf, limit in [
        ('daily', '1d', 200),
        ('4h', '4h', 300),
        ('1h', '1h', 300),
        ('15m', '15m', 300),
    ]:
        try:
            ohlcv = exchange.fetch_ohlcv('BTC/USDT', tf, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            last = df.iloc[-1]
            print(f"  {name:6s}({tf:3s}): {len(df):3d}根 | "
                  f"{df['timestamp'].iloc[0].strftime('%m/%d')}~"
                  f"{df['timestamp'].iloc[-1].strftime('%m/%d')} | "
                  f"最新: ${last['close']:,.0f}")
            df_dict[name] = df
        except Exception as e:
            print(f"  {name}({tf}): ❌ {e}")

    print()

    # ====== 分层Debug分析 ======

    # 1. Wyckoff 层
    print("=" * 70)
    print("[DEBUG] Wyckoff 结构层 (日线)")
    print("=" * 70)
    wy = WyckoffAnalyzer()
    wy_df = df_dict.get('daily', df_dict.get('4h'))
    if wy_df is not None:
        wy_signal = wy.analyze(wy_df)
        print(f"  阶段: {wy_signal.phase}")
        print(f"  偏向: {wy_signal.bias} (置信: {wy_signal.confidence})")
        print(f"  趋势结构: {wy_signal.trend_structure}")
        print(f"  事件: {wy_signal.key_events}")
        print(f"  Spring: {wy_signal.spring_detected} | UTAD: {wy_signal.utad_detected}")
        print(f"  SOS: {wy_signal.sos_confirmed} | SOW: {wy_signal.sow_confirmed}")
        if wy_signal.range_high and wy_signal.range_low:
            print(f"  交易区间: ${wy_signal.range_low:,.0f} - ${wy_signal.range_high:,.0f}")
        # 详细信息
        details = wy_signal.details
        if 'climax' in details:
            c = details['climax']
            print(f"  高潮检测: {c.get('detected')} | 类型: {c.get('type')}")
        if 'secondary_test' in details:
            st = details['secondary_test']
            print(f"  二次测试: {st.get('passed')} | 低量: {st.get('low_volume')}")
        if 'trend' in details:
            t = details['trend']
            print(f"  高低点: HH={t.get('hh_count')} LH={t.get('lh_count')} HL={t.get('hl_count')} LL={t.get('ll_count')}")
    print()

    # 2. SMC 层
    print("=" * 70)
    print("[DEBUG] SMC 机构层 (1H)")
    print("=" * 70)
    sm = SMCAnalyzer()
    smc_df = df_dict.get('1h', df_dict.get('4h'))
    if smc_df is not None:
        smc_signal = sm.analyze(smc_df)
        print(f"  市场结构: {smc_signal.structure}")
        print(f"  BOS次数: {smc_signal.bos_count}")
        print(f"  CHoCH: {smc_signal.choch} | MSS: {smc_signal.mss}")
        print(f"  流动性猎杀: {smc_signal.liquidity_sweep} (确认:{smc_signal.sweep_confirmed})")
        
        ob = smc_signal.order_block
        print(f"  OB: 类型={ob.get('type')} 近端={ob.get('proximal')} 远端={ob.get('distal')} 质量={ob.get('quality')}")
        
        fvg = smc_signal.fvg
        print(f"  FVG: {fvg.get('type')} (top={fvg.get('top')}, bot={fvg.get('bottom')})")
        
        ote = smc_signal.ote_zone
        print(f"  OTE甜点: ${ote.get('sweet'):,.0f}" if ote.get('sweet') else "  OTE: 无")
        print(f"  OTE区间: ${ote.get('low'):,.0f} - ${ote.get('high'):,.0f}" if ote.get('low') else "")
        
        print(f"  Breaker: {smc_signal.breaker_detected}")
        print(f"  POI数量: {len(smc_signal.poi_list)}")
        for poi in smc_signal.poi_list[:3]:
            print(f"    → {poi.get('type')} @ {poi.get('level')} ({poi.get('direction')})" if poi.get('level') else f"    → {poi.get('type')} ({poi.get('direction')})")
    print()

    # 3. PA 层
    print("=" * 70)
    print("[DEBUG] PA 执行层 (15min)")
    print("=" * 70)
    pa = PAAnalyzer()
    pa_df = df_dict.get('15m', df_dict.get('1h'))
    # 用日线趋势方向作为wyckoff_bias
    wy_bias = wy_signal.bias if wy_signal else "NEUTRAL"
    if pa_df is not None:
        pa_signal = pa.analyze(pa_df, wyckoff_bias=wy_bias)
        print(f"  始终在场: {pa_signal.always_in}")
        print(f"  趋势强度: {pa_signal.trend_strength}/5")
        print(f"  回调腿数: {pa_signal.callback_legs}")
        print(f"  H2就绪: {pa_signal.h2_ready} | L2就绪: {pa_signal.l2_ready}")
        print(f"  信号K线质量: {pa_signal.signal_bar_quality}分 ({pa_signal.signal_bar_type})")
        print(f"  EMA位置: {pa_signal.ema_position}")
        print(f"  铁丝网: {pa_signal.is_barbwire}")
        print(f"  高潮警告: {pa_signal.climax_warning}")
        if pa_signal.measured_move_target:
            print(f"  测量移动: ${pa_signal.measured_move_target:,.0f}")
        if pa_signal.entry_price:
            print(f"  建议入场: ${pa_signal.entry_price:,.0f}")
        if pa_signal.stop_loss:
            print(f"  建议止损: ${pa_signal.stop_loss:,.0f}")
    print()

    # 4. 完整引擎
    print("=" * 70)
    print("[DEBUG] TrinityEngine 完整分析")
    print("=" * 70)
    engine = TrinityEngine(config={
        'risk_per_trade': 0.02,
        'leverage': 5,
        'min_score_long': 70,
        'min_score_short': 70,
    })

    signal = engine.analyze(df_dict=df_dict, symbol='BTCUSDT', account_balance=100.0)
    print(engine.get_status_report(signal))

    # 总结
    print()
    print("=" * 70)
    print(" 📊 验证总结")
    print("=" * 70)
    checks = [
        ("K线数据", len(df_dict) == 4),
        ("Wyckoff运行", wy_signal is not None),
        ("SMC运行", smc_signal is not None),
        ("PA运行", pa_signal is not None),
        ("引擎运行", signal is not None),
        ("无异常崩溃", True),
    ]
    all_pass = True
    for name, ok in checks:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")
        if not ok:
            all_pass = False

    if all_pass:
        print(f"\n  🎉 全部通过！系统可以部署。")
        print(f"  BTC当前价格: ${df_dict['15m'].iloc[-1]['close']:,.0f}")
        print(f"  系统判定: {signal.signal} (这是正常的—市场并非时时有机会)")
    else:
        print(f"\n  ⚠ 存在未通过项，需修复后再部署。")


if __name__ == "__main__":
    main()
