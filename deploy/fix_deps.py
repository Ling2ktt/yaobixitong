"""修复服务器 venv + 重启"""
import paramiko

H = '47.79.86.112'
P = 'Lxk828221'
R = '/opt/wangcai'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("Connect...")
c.connect(H, port=22, username='root', password=P, timeout=15)
print("OK\n")

def run(cmd, t=120):
    stdin, stdout, stderr = c.exec_command(cmd, get_pty=True, timeout=t)
    out = stdout.read().decode(errors='ignore').strip()
    err = stderr.read().decode(errors='ignore').strip()
    if out:
        for line in out.split('\n')[:25]:
            if line.strip(): print("  " + line)
    if err and 'WARN' not in err:
        print("  [e] " + err[:200])
    return out

# 1
print("[1/6] Install python3-venv...")
run("apt-get update -qq && apt-get install -y -qq python3-venv 2>&1 | tail -3")

# 2
print("\n[2/6] Recreate venv...")
run("cd " + R + " && rm -rf venv && python3 -m venv venv")

# 3
print("\n[3/6] Install Python dependencies...")
cmd = "cd " + R + " && source venv/bin/activate && pip install -q --upgrade pip && pip install -q ccxt flask loguru pandas numpy psutil pyyaml python-dotenv aiohttp requests schedule 2>&1 | tail -5"
run(cmd, 300)

# 4
print("\n[4/6] Verify imports...")
result = run("cd " + R + " && source venv/bin/activate && python3 -c 'import ccxt,flask,loguru;print(\"All OK\")'")

# 5
print("\n[5/6] Restart systemd services...")
run("systemctl restart wangcai")
run("systemctl restart wangcai-web")
import time; time.sleep(5)
r1 = run("systemctl is-active wangcai")
r2 = run("systemctl is-active wangcai-web")
print("  Backend: " + r1.strip())
print("  Frontend: " + r2.strip())

# 6
print("\n[6/6] Check API and logs...")
time.sleep(3)
api = run("curl -s --max-time 5 http://localhost:8080/api/status")
print("  API: " + api[:200])

print("\n  Backend log:")
run("tail -10 " + R + "/logs/wangcai.log")

c.close()
print("\n" + "=" * 60)
print("  ✅ DONE!")
print("  http://47.79.86.112:8080")
print("=" * 60)
