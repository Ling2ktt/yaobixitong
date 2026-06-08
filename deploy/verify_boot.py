import paramiko, time
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('47.79.86.112', port=22, username='root', password='Lxk828221', timeout=15)

def run(cmd):
    si, so, se = c.exec_command(cmd, get_pty=True, timeout=15)
    return so.read().decode(errors='ignore').strip()

print("Enabling + restarting...")
run('systemctl daemon-reload')

for svc in ['wangcai', 'wangcai-web', 'wangcai-watchdog']:
    run(f'systemctl enable {svc}')
    run(f'systemctl restart {svc}')

time.sleep(8)
print()

for svc in ['wangcai', 'wangcai-web', 'wangcai-watchdog']:
    a = run(f'systemctl is-active {svc}')
    e = run(f'systemctl is-enabled {svc}')
    print(f"  {svc}: active={a.strip()}  boot={e.strip()}")

print()
print("API:", run('curl -s --max-time 5 http://localhost:8080/api/status'))
print("Signal:", run('curl -s --max-time 5 http://localhost:8080/api/strategy_status')[:200])
print("Watchdog:", run('tail -3 /opt/wangcai/logs/watchdog.log 2>/dev/null || echo empty'))

print("\n✅ http://47.79.86.112:8080")
c.close()
