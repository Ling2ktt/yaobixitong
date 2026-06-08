"""最终重启 - 修复合约余额"""
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
        for line in out.split('\n')[:25]:
            if line.strip(): print("  " + line)
    return out

# 1. Force kill everything
print("[1] Kill all Python...")
run("pkill -9 python3 2>/dev/null; sleep 2; echo done")

# 2. Verify config
print("\n[2] Config status...")
run("cd " + R + " && grep -E 'mode:|default_type|preferred_markets|decision_mode' config/system.yaml")

# 3. Verify systemd
print("\n[3] Systemd service...")
run("grep ExecStart /etc/systemd/system/wangcai.service")

# 4. Start backend manually first to see if it works
print("\n[4] Start backend...")
run("cd " + R + " && source venv/bin/activate && nohup python3 main.py --mode live > logs/wangcai.log 2>&1 &")
time.sleep(8)

# 5. Check
print("\n[5] Verify...")
ps_check = run("ps aux | grep main.py | grep -v grep")
api_check = run("curl -s --max-time 5 http://localhost:8080/api/status")
account_check = run("curl -s --max-time 5 http://localhost:8080/api/account")

print("\n  Process: " + ("RUNNING" if ps_check else "DEAD"))
print("  API: " + (api_check[:200] if api_check else "EMPTY"))
print("  Account: " + (account_check[:300] if account_check else "EMPTY"))

# 6. Logs
print("\n[6] Latest log...")
run("tail -25 " + R + "/logs/wangcai.log")

c.close()
print("\n✅ http://47.79.86.112:8080")
