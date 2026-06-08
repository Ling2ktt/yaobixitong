"""Fix syntax + restart"""
import paramiko, time

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('47.79.86.112', port=22, username='root', password='Lxk828221', timeout=15)
print("Connected\n")

def run(cmd, t=20):
    si, so, se = c.exec_command(cmd, get_pty=True, timeout=t)
    out = so.read().decode(errors='ignore').strip()
    if out:
        for line in out.split('\n')[:15]:
            if line.strip(): print("  " + line)
    return out

# Fix the syntax error by uploading clean local copy
print("[1] Upload clean market_data.py...")
s = c.open_sftp()
s.put(r"D:\workbuddy\wangcai-trading-bot\modules\market_data.py", "/opt/wangcai/modules/market_data.py")
s.close()
print("  Uploaded")

# Test syntax
print("\n[2] Check syntax...")
run("cd /opt/wangcai && python3 -m py_compile modules/market_data.py && echo OK")

# Kill + restart
print("\n[3] Restart services...")
run("systemctl restart wangcai-web")
run("systemctl restart wangcai")
time.sleep(10)

# Verify
print("\n[4] Verify...")
run("systemctl is-active wangcai")
run("systemctl is-active wangcai-web")
run("curl -s --max-time 5 http://localhost:8080/api/status")
run("curl -s --max-time 5 http://localhost:8080/api/account")

print("\n[5] Latest log...")
run("tail -20 /opt/wangcai/logs/wangcai.log")

c.close()
print("\nDone! http://47.79.86.112:8080")
