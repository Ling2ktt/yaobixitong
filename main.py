#!/usr/bin/env python3
"""
旺财自动交易系统 - 入口文件
WangCai Auto Trading System - Entry Point

用法:
    python main.py                    # 启动主循环
    python main.py --status           # 查看状态
    python main.py --test-modules     # 测试各模块
    python main.py --backtest         # 回测模式

环境变量:
    复制 .env.example 为 .env 并填写你的API密钥
"""

import sys, os
# Windows 下修复 emoji 编码
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import argparse
import asyncio
import json
import sys
from pathlib import Path

from loguru import logger

from core.engine import WangCaiEngine
from core.config_loader import load_config


def print_banner():
    """打印启动横幅"""
    banner = """
    ╔═══════════════════════════════════════════╗
    ║                                           ║
    ║     🐕 旺财自动交易系统 v1.0.0            ║
    ║     WangCai Auto Trading System           ║
    ║                                           ║
    ║     Binance + OKX 双源互补               ║
    ║     AI 驱动 · 风控护航 · 模块独立         ║
    ║                                           ║
    ╚═══════════════════════════════════════════╝
    """
    print(banner)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='旺财自动交易系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 正常启动
  python main.py --config custom.yaml  # 使用自定义配置
  python main.py --status             # 查看系统状态
  python main.py --test-modules       # 测试各模块功能
        """
    )
    
    parser.add_argument(
        '-c', '--config',
        default='config/system.yaml',
        help='配置文件路径 (默认: config/system.yaml)'
    )
    
    parser.add_argument(
        '--status',
        action='store_true',
        help='查看系统状态'
    )
    
    parser.add_argument(
        '--test-modules',
        action='store_true',
        help='测试各模块功能'
    )
    
    parser.add_argument(
        '--mode',
        choices=['paper', 'live'],
        help='覆盖交易模式 (paper=模拟, live=实盘)'
    )
    
    parser.add_argument(
        '--symbols',
        nargs='+',
        help='覆盖监控的交易对，如: BTC/USDT ETH/USDT'
    )
    
    return parser.parse_args()


async def test_modules(engine: WangCaiEngine):
    """测试各模块功能"""
    print("\n🔧 模块功能测试\n")
    
    # 1. 测试行情模块
    print("[1/6] 测试行情数据模块...")
    try:
        health = engine.market_data.health_check()
        print(f"   ✅ 交易所健康状态: {health}")
        
        symbol = engine.market_data.symbols[0] if engine.market_data.symbols else 'BTC/USDT'
        snapshot = await engine.market_data.get_market_snapshot(symbol)
        print(f"   ✅ 市场快照: {symbol} = ${snapshot.avg_price:.2f}")
        
        indicators = engine.market_data.get_technical_indicators(symbol)
        print(f"   ✅ 技术指标: {list(indicators.keys())}")
    except Exception as e:
        print(f"   ❌ 行情模块测试失败: {e}")
    
    # 2. 测试信息聚合
    print("\n[2/6] 测试信息聚合模块...")
    try:
        info = await engine.info_aggregator.aggregate(engine.market_data.exchanges)
        print(f"   ✅ 信息聚合完成 | 情绪: {info.overall_sentiment:.2f}")
        print(f"   ✅ 信息条目: {len(info.items)}")
    except Exception as e:
        print(f"   ❌ 信息聚合测试失败: {e}")
    
    # 3. 测试账户管理
    print("\n[3/6] 测试账户管理模块...")
    try:
        portfolio = await engine.account_manager.sync_all()
        print(f"   ✅ 账户同步完成")
        print(f"   ✅ 总权益: ${portfolio.total_equity:,.2f}")
        print(f"   ✅ 总持仓: {portfolio.total_positions}")
    except Exception as e:
        print(f"   ❌ 账户管理测试失败: {e}")
    
    # 4. 测试AI决策（需要API密钥）
    print("\n[4/7] 测试AI决策模块...")
    try:
        if engine.ai_decision._client:
            decision = await engine.ai_decision.decide(
                market_data={'summary': 'BTC/USDT 价格 $65000', 'indicators': {'rsi': 55}},
                info={'summary': '市场情绪中性', 'overall_sentiment': 0.1},
                account={'summary': '总权益 $10000', 'positions': []}
            )
            print(f"   ✅ AI决策完成 | 动作: {decision.action.value} | 置信度: {decision.confidence:.2f}")
        else:
            print("   ⚠️ AI客户端未初始化（缺少API密钥）")
    except Exception as e:
        print(f"   ❌ AI决策测试失败: {e}")
    
    # 5. 测试规则策略模块（QuantTrend）
    print("\n[5/7] 测试规则策略模块（QuantTrend）...")
    try:
        import pandas as pd
        # 用模拟数据测试策略
        dates = pd.date_range('2024-01-01', periods=300, freq='4h')
        np.random.seed(42)
        prices = 50000 + np.cumsum(np.random.randn(300) * 500)
        
        df = pd.DataFrame({
            'open': prices + np.random.randn(300) * 100,
            'high': prices + abs(np.random.randn(300) * 200),
            'low': prices - abs(np.random.randn(300) * 200),
            'close': prices,
            'volume': np.random.randint(100, 1000, 300)
        }, index=dates)
        df.index.name = 'timestamp'
        df = df.reset_index()
        
        signal = engine.quant_strategy.generate_signal(df, symbol="BTC/USDT")
        print(f"   ✅ 策略信号生成: {signal.signal.value}")
        print(f"   ✅ 趋势评分: {signal.score:.2f}")
        print(f"   ✅ 杠杆: {signal.leverage}x")
        print(f"   ✅ 理由: {signal.reason[:60]}...")
    except Exception as e:
        print(f"   ❌ 规则策略测试失败: {e}")
        import traceback
        traceback.print_exc()
    print("\n[5/6] 测试风控模块...")
    try:
        risk_report = engine.risk_control.check(
            decision={'action': 'BUY', 'symbol': 'BTC/USDT', 'amount': 100, 'price': 65000},
            account={'total_equity': 10000, 'available_usdt': 5000, 'position_count': 2},
            daily_stats={'total_pnl': 100}
        )
        print(f"   ✅ 风控审核完成 | 结果: {risk_report.overall_level.value} | 通过: {risk_report.is_passed}")
    except Exception as e:
        print(f"   ❌ 风控测试失败: {e}")
    
    # 6. 测试通知模块
    print("\n[6/6] 测试记录通知模块...")
    try:
        from modules.logger_notifier import AlertMessage, AlertLevel
        await engine.logger_notifier.notify(AlertMessage(
            level=AlertLevel.INFO,
            title="模块测试完成",
            content="所有模块测试已通过！",
            tags=['test']
        ))
        print("   ✅ 通知发送成功")
    except Exception as e:
        print(f"   ❌ 通知测试失败: {e}")
    
    print("\n✨ 模块测试完成！")


def print_status(engine: WangCaiEngine):
    """打印系统状态"""
    status = engine.get_status()
    print("\n📊 旺财系统状态\n")
    print(json.dumps(status, indent=2, ensure_ascii=False))
    print()


def main():
    """主函数"""
    args = parse_args()
    
    print_banner()
    
    # 检查配置文件
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ 配置文件不存在: {args.config}")
        print("   请复制 .env.example 为 .env 并填写API密钥")
        print("   或创建配置文件: config/system.yaml")
        sys.exit(1)
    
    # 初始化引擎
    try:
        engine = WangCaiEngine(config_path=args.config)
    except Exception as e:
        print(f"❌ 引擎初始化失败: {e}")
        sys.exit(1)
    
    # 覆盖配置
    if args.mode:
        engine.system_config['mode'] = args.mode
        print(f"⚙️  模式已覆盖为: {args.mode}")
    
    if args.symbols:
        engine.market_data.symbols = args.symbols
        print(f"⚙️  监控标的已覆盖: {args.symbols}")
    
    # 执行命令
    if args.status:
        print_status(engine)
    elif args.test_modules:
        asyncio.run(test_modules(engine))
    else:
        # 正常启动
        try:
            asyncio.run(engine.run())
        except KeyboardInterrupt:
            print("\n👋 收到中断信号，正在退出...")
            asyncio.run(engine.shutdown())


if __name__ == "__main__":
    main()
