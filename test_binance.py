#!/usr/bin/env python3
"""
快速测试 Binance API 连接
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import ccxt
from loguru import logger
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.environ.get('BINANCE_API_KEY', '')
api_secret = os.environ.get('BINANCE_API_SECRET', '')

print(f"\n{'='*50}")
print("  🔌 Binance API 连接测试")
print(f"{'='*50}")

print(f"\n[1] 检查 API 密钥...")
print(f"  API Key:    {api_key[:20]}..." if api_key else "  ❌ API Key 为空！")
print(f"  API Secret: {api_secret[:20]}..." if api_secret else "  ❌ API Secret 为空！")

if not api_key or not api_secret:
    print("\n❌ 请在 .env 文件中填写正确的 API 密钥")
    sys.exit(1)

# ── 测试公开接口（无需密钥）──
print(f"\n[2] 测试公开接口（无需密钥）...")
try:
    public_exchange = ccxt.binance({'enableRateLimit': True})
    ticker = public_exchange.fetch_ticker('BTC/USDT')
    price = ticker['last']
    print(f"  ✅ 公开接口正常 | BTC/USDT = ${price:,.2f}")
except Exception as e:
    print(f"  ❌ 公开接口失败: {e}")
    sys.exit(1)

# ── 测试私有接口（需要密钥）──
print(f"\n[3] 测试私有接口（需要 API 密钥）...")
try:
    private_exchange = ccxt.binance({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'options': {'defaultType': 'spot', 'test': True}  # 测试网
    })
    # 尝试获取账户信息（测试网）
    balance = private_exchange.fetch_balance()
    usdt = balance.get('USDT', {}).get('free', 0)
    print(f"  ✅ 私有接口正常（测试网）")
    print(f"     USDT 余额: ${usdt:,.2f}")
except ccxt.AuthenticationError as e:
    print(f"  ❌ API 密钥无效: {e}")
    print(f"  💡 请检查：")
    print(f"     1. API Key 和 Secret 是否正确")
    print(f"     2. API Key 是否已启用（Binance 后台）")
    print(f"     3. IP 限制是否开启（可暂时关闭测试）")
    sys.exit(1)
except ccxt.NetworkError as e:
    print(f"  ⚠️ 网络错误（可能是防火墙/代理问题）: {e}")
except Exception as e:
    print(f"  ⚠️ 其他错误: {e}")

# ── 测试实盘接口（警告）──
print(f"\n[4] 测试实盘接口（sandbox=false）...")
try:
    live_exchange = ccxt.binance({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
    balance = live_exchange.fetch_balance()
    usdt = balance.get('USDT', {}).get('free', 0)
    btc = balance.get('BTC', {}).get('free', 0)
    print(f"  ✅ 实盘接口连接成功！")
    print(f"     USDT: ${usdt:,.2f}")
    print(f"     BTC:  {btc:.8f}")
    print(f"\n  ⚠️  注意：当前 config/system.yaml 中 sandbox: true")
    print(f"     如需实盘交易，请设置为 sandbox: false")
except ccxt.AuthenticationError as e:
    print(f"  ❌ 实盘 API 密钥无效: {e}")
except Exception as e:
    print(f"  ⚠️ {e}")

print(f"\n{'='*50}")
print("  ✅ 测试完成")
print(f"{'='*50}\n")
print("💡 下一步：")
print("  1. 如果测试通过，运行：python main.py --mode paper")
print("  2. 观察日志，确认信号生成正常")
print("  3. 确认无误后，修改 config/system.yaml 中 sandbox: false 启用实盘\n")
