"""最终修复：合约余额 + 服务重启"""
import paramiko, time

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
        for line in out.split('\n')[:30]:
            if line.strip(): print("  " + line)
    if err and 'WARN' not in err and 'warning' not in err.lower():
        print("  [e] " + err[:200])
    return out

# 1. Fix systemd to run LIVE mode
print("[1/5] 修改 systemd 服务为实盘模式")
run(f"sed -i 's/--mode paper/--mode live/' /etc/systemd/system/wangcai.service && grep 'mode' /etc/systemd/system/wangcai.service")
run("systemctl daemon-reload")

# 2. Fix market_data symbol for swap - use BTC/USDT (no :USDT suffix for data)
print("\n[2/5] 修复 market_data 符号兼容")
run(f"""cd {R} && python3 << 'PYEOF'
import yaml
cfg = yaml.safe_load(open('config/system.yaml'))
# Keep BTC/USDT symbol for market data, only use swap for trading
cfg['exchanges']['binance']['preferred_markets'] = ['BTC/USDT']
cfg['exchanges']['binance']['default_type'] = 'swap'
yaml.dump(cfg, open('config/system.yaml','w'), allow_unicode=True, sort_keys=False)
print('Config fixed')
PYEOF
grep preferred_markets config/system.yaml
""")

# 3. Kill stuck processes
print("\n[3/5] 清理卡住的进程")
run("pkill -9 -f main.py 2>/dev/null; pkill -9 -f web_server 2>/dev/null; sleep 1; echo done")

# 4. Restart both services
print("\n[4/5] 重启服务")
run("systemctl restart wangcai")
run("systemctl restart wangcai-web")
time.sleep(8)

# 5. Verify
print("\n[5/5] 验证")
print("  Backend:", run("systemctl is-active wangcai"))
print("  Frontend:", run("systemctl is-active wangcai-web"))

print("\n  API:", run("curl -s --max-time 5 http://localhost:8080/api/status"))
print("  Account:", run("curl -s --max-time 5 http://localhost:8080/api/account")[:500])

print("\n  Latest log:")
run("tail -15 " + R + "/logs/wangcai.log")

c.close()
print("\n✅ Done! http://47.79.86.112:8080")
