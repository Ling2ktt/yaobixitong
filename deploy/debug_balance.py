"""排查 Binance 账户资金问题"""
import paramiko

H = '47.79.86.112'
P = 'Lxk828221'
R = '/opt/wangcai'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("🔌 SSH...")
c.connect(H, port=22, username='root', password=P, timeout=15)
print("✅ 已连接\n")

def run(cmd, t=120):
    stdin, stdout, stderr = c.exec_command(cmd, get_pty=True, timeout=t)
    out = stdout.read().decode(errors='ignore').strip()
    err = stderr.read().decode(errors='ignore').strip()
    if out:
        for line in out.split('\n')[:40]:
            if line.strip(): print("  " + line)
    if err and 'WARN' not in err:
        print("  [e] " + err[:300])
    return out

# 1. 直接用 ccxt 测试 Binance API 余额
print("=" * 60)
print("[1] 直接用 ccxt 查询 Binance 余额")
print("=" * 60)

run(f"""
cd {R} && source venv/bin/activate
python3 << 'EOF'
import ccxt, json

# Read API keys from .env
with open('.env') as f:
    lines = f.readlines()
key = next(l.split('=')[1].strip() for l in lines if l.startswith('BINANCE_API_KEY') and l.strip())
secret = next(l.split('=')[1].strip() for l in lines if l.startswith('BINANCE_API_SECRET') and l.strip())

print("API Key:", key[:20] + "...")
print("Secret:", secret[:20] + "...")

# Create exchange
exchange = ccxt.binance({{
    'apiKey': key,
    'secret': secret,
    'enableRateLimit': True,
    'options': {{'defaultType': 'spot'}}
}})

try:
    balance = exchange.fetch_balance()
    print("\\n=== Balances ===")
    for asset in ['USDT', 'BTC', 'ETH', 'BNB', 'BUSD', 'USDC']:
        b = balance.get(asset, {{}})
        if b:
            free = b.get('free', 0) or 0
            used = b.get('used', 0) or 0
            total = b.get('total', 0) or 0
            print(f"  {{asset}}: free={{free}} used={{used}} total={{total}}")

    print("\\n=== Balance keys ===")
    print(list(balance.keys())[:20])

    total_usdt = balance.get('total', {{}}).get('USDT', 0)
    free_usdt = balance.get('free', {{}}).get('USDT', 0)
    print(f"\\n  total USDT: {{total_usdt}}")
    print(f"  free USDT: {{free_usdt}}")

except Exception as e:
    print(f"ERROR: {{e}}")
    import traceback
    traceback.print_exc()
EOF
""", t=30)

# 2. Check what the account_manager module sees
print("\n" + "=" * 60)
print("[2] 查看 account_manager.py 的资金解析逻辑")
print("=" * 60)

run(f"cd {R} && grep -n 'total_usdt\|fetch_balance\|USDT\|balance.get' modules/account_manager.py | head -20")

print("\n" + "=" * 60)
print("[3] 修复方案: 增强资金解析")
print("=" * 60)

# The issue might be that ccxt returns balance in nested structure
# and the current code isn't parsing it correctly for the live API
# Let me check the exact balance format ccxt returns

c.close()
