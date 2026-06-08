import paramiko

H = '47.79.86.112'
P = 'Lxk828221'
R = '/opt/wangcai'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(H, port=22, username='root', password=P, timeout=15)
print("Connected\n")

# Check all wallet types
cmd = """cd /opt/wangcai && source venv/bin/activate && python3 -c "
import ccxt
with open('.env') as f:
    lines = f.readlines()
key = next(l.split('=')[1].strip() for l in lines if 'BINANCE_API_KEY' in l and l.strip())
secret = next(l.split('=')[1].strip() for l in lines if 'BINANCE_API_SECRET' in l and l.strip())

# Spot
print('=== Spot ===')
spot = ccxt.binance({'apiKey':key,'secret':secret,'enableRateLimit':True,'options':{'defaultType':'spot'}})
bal = spot.fetch_balance()
for a in ['USDT','BTC','ETH','BNB']:
    b = bal.get(a,{})
    if b.get('free') or b.get('total'):
        print('  '+a+': free='+str(b.get('free',0))+' total='+str(b.get('total',0)))

# Futures (U-margined)
print()
print('=== Futures (swap) ===')
try:
    fut = ccxt.binance({'apiKey':key,'secret':secret,'enableRateLimit':True,'options':{'defaultType':'swap'}})
    bal2 = fut.fetch_balance()
    for a in ['USDT','BTC','ETH','BNB']:
        b = bal2.get(a,{})
        if b.get('free') or b.get('total'):
            print('  '+a+': free='+str(b.get('free',0))+' total='+str(b.get('total',0)))
    # Show position info
    try:
        pos = fut.fetch_positions(['BTC/USDT:USDT'])
        for p in pos:
            if p.get('contracts'):
                print('  Position: '+p['symbol']+' contracts='+str(p.get('contracts',0))+' pnl='+str(p.get('unrealizedPnl',0)))
    except: pass
except Exception as e:
    print('  ERROR: '+str(e))
" 2>&1
"""
si, so, se = c.exec_command(cmd, get_pty=True, timeout=30)
out = so.read().decode(errors='ignore')
err = se.read().decode(errors='ignore')
print(out)
if err: print("ERR:", err[:200])

c.close()
print("\nDone")
