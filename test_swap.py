#!/usr/bin/env python3
"""测试 swap 模式下各种操作"""
import ccxt, sys

# Read API keys
with open('.env') as f:
    lines = f.readlines()
key = next(l.split('=')[1].strip() for l in lines if l.startswith('BINANCE_API_KEY') and l.strip())
secret = next(l.split('=')[1].strip() for l in lines if l.startswith('BINANCE_API_SECRET') and l.strip())

print("=== Test 1: Spot connection (fetch_ohlcv) ===")
try:
    spot = ccxt.binance({'apiKey': key, 'secret': secret, 'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
    ohlcv = spot.fetch_ohlcv('BTC/USDT', '4h', limit=5)
    print(f"OK - Got {len(ohlcv)} candles")
    print(f"Latest: {ohlcv[-1][4]}")
except Exception as e:
    print(f"FAIL: {e}")

print("\n=== Test 2: Swap connection (fetch_balance) ===")
try:
    swap = ccxt.binance({'apiKey': key, 'secret': secret, 'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    bal = swap.fetch_balance()
    print(f"OK - USDT: {bal.get('USDT', {}).get('free', 0)}")
except Exception as e:
    print(f"FAIL: {e}")

print("\n=== Test 3: Swap connection (fetch_ohlcv) ===")
try:
    ohlcv2 = swap.fetch_ohlcv('BTC/USDT', '4h', limit=5)
    print(f"OK - Got {len(ohlcv2)} candles")
    print(f"Latest: {ohlcv2[-1][4]}")
except Exception as e:
    print(f"FAIL: {e}")

print("\n=== Test 4: Swap connection (fetch_ticker) ===")
try:
    ticker = swap.fetch_ticker('BTC/USDT:USDT')
    print(f"OK - Price: {ticker.get('last', 'N/A')}")
except Exception as e:
    print(f"FAIL: {e}")

print("\n=== Test 5: Spot + Swap dual approach ===")
try:
    # Keep spot for data, swap for balance
    bal2 = swap.fetch_balance()
    ohlcv3 = spot.fetch_ohlcv('BTC/USDT', '4h', limit=3)
    print(f"OK - Balance: ${bal2.get('USDT', {}).get('free', 0)}")
    print(f"OK - OHLCV: {len(ohlcv3)} candles, latest price ${ohlcv3[-1][4]}")
except Exception as e:
    print(f"FAIL: {e}")

print("\n✅ All tests done")
