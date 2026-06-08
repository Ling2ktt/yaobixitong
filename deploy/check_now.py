import paramiko, sys, os

os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

H = '47.79.86.112'
P = 'Lxk828221'

print("1. Connecting...", flush=True)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    c.connect(H, port=22, username='root', password=P, timeout=15, banner_timeout=25)
    print("OK\n", flush=True)
except Exception as e:
    print("FAIL:", e, flush=True)
    sys.exit(1)

def run(cmd, timeout=15):
    si, so, se = c.exec_command(cmd, get_pty=True, timeout=timeout)
    return so.read().decode(errors='ignore').strip(), se.read().decode(errors='ignore').strip()

# Quick checks - each in separate SSH call
print("2. Services:", flush=True)
for s in ['wangcai', 'wangcai-web', 'wangcai-watchdog']:
    out, err = run(f"systemctl is-active {s} && systemctl is-enabled {s}")
    print(f"  {s}: {out}", flush=True)

print("\n3. Processes:", flush=True)
out, _ = run("ps aux|grep -E 'main.py|web_server'|grep -v grep|wc -l")
print(f"  Count: {out}", flush=True)

print("\n4. API:", flush=True)
out, _ = run("curl -s --max-time 8 http://localhost:8080/api/status")
print(f"  {out[:200]}", flush=True)

out, _ = run("curl -s --max-time 8 http://localhost:8080/api/account")
print(f"  Account: {out[:200]}", flush=True)

out, _ = run("curl -s --max-time 8 http://localhost:8080/api/strategy_status")
print(f"  Signal: {out[:200]}", flush=True)

print("\n5. Recent cycles:", flush=True)
out, _ = run("grep '主循环.*开始' /opt/wangcai/logs/wangcai.log | tail -5")
print(out or "None", flush=True)

out, _ = run("grep 'Decision.*Rule' /opt/wangcai/logs/wangcai.log | tail -3")
print(out or "None", flush=True)

print("\n6. Resources:", flush=True)
out, _ = run("free -h | grep Mem")
print(out, flush=True)

c.close()
print("\nDone", flush=True)
