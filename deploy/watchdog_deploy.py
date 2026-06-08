"""部署守护进程 + 加固 systemd"""
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
    if out:
        for l in out.split('\n')[:30]:
            if l.strip(): print("  " + l)
    return out

# 1. Upload watchdog
print("[1] Upload watchdog...")
s = c.open_sftp()
s.put(r"D:\workbuddy\wangcai-trading-bot\deploy\watchdog.py", f"{R}/watchdog.py")
s.close()
print("  ✅ uploaded")

# 2. Update wangcai.service - add robustness
print("\n[2] Update systemd services...")
run(f"""cat > /etc/systemd/system/wangcai.service << 'EOF'
[Unit]
Description=旺财自动交易系统
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory={R}
Environment="PYTHONUNBUFFERED=1"
Environment="PATH={R}/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart={R}/venv/bin/python3 {R}/main.py --mode live
Restart=always
RestartSec=30
TimeoutStartSec=120
TimeoutStopSec=30
KillMode=mixed
KillSignal=SIGTERM
SendSIGKILL=yes

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/wangcai-web.service << 'EOF'
[Unit]
Description=旺财 Web 前端
After=network-online.target wangcai.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory={R}
Environment="PYTHONUNBUFFERED=1"
Environment="PATH={R}/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart={R}/venv/bin/python3 {R}/web_server.py
Restart=always
RestartSec=10
TimeoutStartSec=60

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/wangcai-watchdog.service << 'EOF'
[Unit]
Description=旺财守护进程
After=wangcai-web.service
Requires=wangcai.service wangcai-web.service

[Service]
Type=simple
User=root
WorkingDirectory={R}
Environment="PATH={R}/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart={R}/venv/bin/python3 {R}/watchdog.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

echo "✅ systemd files updated"
""")

# 3. Enable all services for boot
print("\n[3] Enable boot auto-start...")
run("systemctl daemon-reload")
for svc in ['wangcai', 'wangcai-web', 'wangcai-watchdog']:
    run(f"systemctl enable {svc}")

# 4. Restart everything
print("\n[4] Restart all services...")
run("systemctl restart wangcai")
run("systemctl restart wangcai-web")
run("systemctl restart wangcai-watchdog")
time.sleep(8)

# 5. Verify
print("\n[5] Status check...")
for svc in ['wangcai', 'wangcai-web', 'wangcai-watchdog']:
    status = run(f"systemctl is-active {svc}")
    enabled = run(f"systemctl is-enabled {svc}")
    print(f"  {svc}: active={status.strip()}, boot={enabled.strip()}")

print("\n[6] API check...")
run("curl -s http://localhost:8080/api/status")
run("curl -s http://localhost:8080/api/strategy_status")

print("\n[7] Watchdog log...")
run(f"tail -5 {R}/logs/watchdog.log 2>/dev/null || echo 'no log yet'")

c.close()
print("\n" + "=" * 60)
print("  ✅ Done!")
print("  3 个服务开机自启: wangcai + web + watchdog")
print("  http://47.79.86.112:8080")
print("=" * 60)
