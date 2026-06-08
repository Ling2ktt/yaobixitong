import paramiko, time, os

H = '47.79.86.112'
P = 'Lxk828221'
R = '/opt/wangcai'
LOCAL = r'D:\workbuddy\wangcai-trading-bot'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(H, port=22, username='root', password=P, timeout=15)
print("Connected")

# Upload 3 files
s = c.open_sftp()
for f in ['web_server.py', 'core/engine.py', 'config/system.yaml']:
    local = os.path.join(LOCAL, f)
    remote = f"{R}/{f}"
    s.put(local, remote)
    print(f"  Uploaded {f}")
s.close()

# Restart both services
def run(cmd):
    si, so, se = c.exec_command(cmd, get_pty=True, timeout=20)
    return so.read().decode(errors='ignore').strip()

run('systemctl restart wangcai')
run('systemctl restart wangcai-web')
time.sleep(8)

# Verify
print("\nStatus:", run('systemctl is-active wangcai'))
print("Frontend:", run('systemctl is-active wangcai-web'))
print("API:", run('curl -s --max-time 5 http://localhost:8080/api/status'))
print("Strategy:", run('curl -s --max-time 5 http://localhost:8080/api/strategy_status'))
print("Account:", run('curl -s --max-time 5 http://localhost:8080/api/account')[:200])

c.close()
print("\n✅ Done! http://47.79.86.112:8080")
