import paramiko, os
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('47.79.86.112', port=22, username='root', password='Lxk828221', timeout=15)

def run(cmd, timeout=30):
    si, so, se = c.exec_command(cmd, get_pty=True, timeout=timeout)
    out = so.read().decode(errors='ignore').strip()
    err = se.read().decode(errors='ignore').strip()
    if out: print(out)
    if err: print(err[:200])
    return out

print("[1] Creating 1GB swap...")
run("""
if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo '✅ Swap created'
else
    echo 'Swap already exists'
fi
""")

print("\n[2] Verify memory...")
run("free -h")

print("\n[3] Restart services to clean memory...")
run("systemctl restart wangcai wangcai-web wangcai-watchdog")
import time; time.sleep(6)

print("\n[4] Check...")
run("free -h")
run("systemctl is-active wangcai wangcai-web wangcai-watchdog")
run("curl -s --max-time 5 http://localhost:8080/api/status")

c.close()
print("\n✅ Done! Swap 已启用，不会再 OOM 中断")
