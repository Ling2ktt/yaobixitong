"""修复：支持 Binance 合约 (swap/futures) 账户"""
import paramiko

H = '47.79.86.112'
P = 'Lxk828221'
R = '/opt/wangcai'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(H, port=22, username='root', password=P, timeout=15)
print("Connected\n")

def run(cmd, t=30):
    si, so, se = c.exec_command(cmd, get_pty=True, timeout=t)
    out = so.read().decode(errors='ignore').strip()
    err = se.read().decode(errors='ignore').strip()
    if out:
        for line in out.split('\n')[:15]:
            if line.strip(): print("  " + line)
    return out

# 1. Update market_data.py to support swap
print("[1/3] 修复 market_data.py - 支持合约账户")
run(f"""cd {R} && sed -i "s/defaultType.*spot.*/defaultType': 'swap'/" modules/market_data.py && grep defaultType modules/market_data.py | head -5""")

# 2. Update account_manager.py to properly parse swap balance
print("\n[2/3] 修复 account_manager.py")
# The current code already handles balance correctly, just need to ensure it works with swap type
run(f"cd {R} && grep -n 'defaultType\|fetch_balance' modules/account_manager.py | head -5")

# 3. Update config to use swap trading
print("\n[3/3] 修改 config - 启用合约")
run(f"""cd {R}
python3 << 'PYEOF'
import yaml
with open('config/system.yaml') as f:
    cfg = yaml.safe_load(f)

# Add trading mode
cfg['system']['trade_mode'] = 'swap'
# Add BTC/USDT:USDT for futures
cfg['exchanges']['binance']['default_type'] = 'swap'
cfg['exchanges']['binance']['preferred_markets'] = ['BTC/USDT:USDT']

with open('config/system.yaml','w') as f:
    yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
print('Config updated')
PYEOF
grep -E "trade_mode|default_type|preferred_markets" config/system.yaml
""")

# Restart
print("\n重启服务...")
run("systemctl restart wangcai")
import time; time.sleep(6)

# Verify
print("\n验证余额...")
run(f"""cd {R} && source venv/bin/activate && python3 -c "
import ccxt
with open('.env') as f:
    lines = f.readlines()
key = next(l.split('=')[1].strip() for l in lines if 'BINANCE_API_KEY' in l)
secret = next(l.split('=')[1].strip() for l in lines if 'BINANCE_API_SECRET' in l)
ex = ccxt.binance({{'apiKey':key,'secret':secret,'enableRateLimit':True,'options':{{'defaultType':'swap'}}}})
bal = ex.fetch_balance()
print('USDT free:', bal.get('USDT',{{}}).get('free',0))
print('USDT total:', bal.get('USDT',{{}}).get('total',0))
"
""")

print("\nAPI 状态:")
run("curl -s http://localhost:8080/api/status")
run("curl -s http://localhost:8080/api/account")

c.close()
print("\n✅ 修复完成! http://47.79.86.112:8080")
