#!/usr/bin/env python3
"""部署旺财到阿里云服务器"""
import paramiko, time, os

H = '47.79.86.112'
PASS = 'Lxk828221'
R = '/opt/wangcai'
LOCAL = r'D:\workbuddy\wangcai-trading-bot'

print("1. Connecting to SSH...")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

for i in range(3):
    try:
        c.connect(H, port=22, username='root', password=PASS, timeout=15, banner_timeout=25)
        print("   Connected!")
        break
    except Exception as e:
        print(f"   Attempt {i+1}: {e}")
        time.sleep(5)
else:
    print("   FAILED")
    exit(1)

# Upload files
print("2. Uploading files...")
s = c.open_sftp()
files = [
    'main.py',
    'web_server.py',
    'modules/market_data.py',
]
for f in files:
    local = os.path.join(LOCAL, f)
    remote = f"{R}/{f}"
    s.put(local, remote)
    print(f"   {f} -> server")
s.close()
print("   Done")

# Stop old processes
print("3. Restarting services...")
c.exec_command('pkill -f main.py || true', timeout=10)
c.exec_command('pkill -f web_server.py || true', timeout=10)
time.sleep(2)
print("   Old processes killed")

# Set decision mode to yanchi
c.exec_command(
    'sed -i "s/decision_mode:.*/decision_mode: \\"yanchi\\"/" ' + R + '/config/system.yaml',
    timeout=10
)

# Start backend
c.exec_command(
    'cd ' + R + ' && source venv/bin/activate && nohup python3 main.py --mode paper > logs/wangcai.log 2>&1 &',
    timeout=10
)
time.sleep(6)
print("   Backend started (or starting)")

# Start frontend
c.exec_command(
    'cd ' + R + ' && source venv/bin/activate && nohup python3 web_server.py > logs/web.log 2>&1 &',
    timeout=10
)
time.sleep(5)
print("   Frontend started (or starting)")

# Verify
print("\n4. Verification...")

si, so, se = c.exec_command('ps aux | grep -E "main.py|web_server" | grep -v grep', timeout=10)
procs = so.read().decode(errors='ignore').strip()
print("   Processes:", "FOUND" if procs else "NONE")

si, so, se = c.exec_command('ss -tlnp | grep 8080', timeout=10)
port = so.read().decode(errors='ignore').strip()
print("   Port 8080:", "LISTENING" if port else "NOT LISTENING")

si, so, se = c.exec_command('curl -s http://localhost:8080/api/status', timeout=10)
api = so.read().decode(errors='ignore').strip()
print("   API Status:", api[:200])

si, so, se = c.exec_command('tail -5 ' + R + '/logs/wangcai.log', timeout=10)
print("\n   Backend log:", so.read().decode(errors='ignore').strip()[:300])

c.close()

print("\n" + "=" * 60)
print("  DEPLOYMENT COMPLETE!")
print("  http://47.79.86.112:8080")
print("=" * 60)
