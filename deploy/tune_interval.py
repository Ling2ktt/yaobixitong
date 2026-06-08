import paramiko, time
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('47.79.86.112', port=22, username='root', password='Lxk828221', timeout=15)

# Upload config
s = c.open_sftp()
s.put(r'D:\workbuddy\wangcai-trading-bot\config\system.yaml', '/opt/wangcai/config/system.yaml')
s.close()
print("Config uploaded")

# Restart
si, so, se = c.exec_command('systemctl restart wangcai', get_pty=True, timeout=15)
time.sleep(8)

# Verify
si, so, se = c.exec_command('systemctl is-active wangcai', get_pty=True, timeout=10)
print("Status:", so.read().decode(errors='ignore').strip())

si, so, se = c.exec_command('grep interval_seconds /opt/wangcai/config/system.yaml', get_pty=True, timeout=10)
print("Interval:", so.read().decode(errors='ignore').strip())

si, so, se = c.exec_command('tail -8 /opt/wangcai/logs/wangcai.log', get_pty=True, timeout=10)
print("Log:\n" + so.read().decode(errors='ignore').strip())

si, so, se = c.exec_command('curl -s http://localhost:8080/api/status', get_pty=True, timeout=10)
print("API:", so.read().decode(errors='ignore').strip())

c.close()
print("Done!")
