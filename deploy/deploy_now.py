"""旺财部署脚本 - 上传前端+启动后端"""
import paramiko, time

H = '47.79.86.112'
PASS = 'Lxk828221'
R = '/opt/wangcai'
L = r'D:\workbuddy\wangcai-trading-bot\web_server.py'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

print('1. Connecting...')
c.connect(H, port=22, username='root', password=PASS, timeout=20, banner_timeout=30)
print('   OK\n')

# Upload
print('2. Upload web_server.py...')
s = c.open_sftp()
s.put(L, R + '/web_server.py')
s.close()
print('   OK\n')

# Restart web
print('3. Restart Web server...')
c.exec_command('pkill -f web_server.py', timeout=10)
time.sleep(1)
c.exec_command('cd ' + R + ' && source venv/bin/activate && nohup python3 web_server.py > logs/web.log 2>&1 &', timeout=10)
time.sleep(4)
si, so, se = c.exec_command('ps aux | grep web_server | grep -v grep', timeout=10)
web_proc = so.read().decode(errors='ignore').strip()
print('   Web:', web_proc[:200] or 'NOT RUNNING')

# Restart wangcai
print('\n4. Start WangCai backend...')
c.exec_command('pkill -f main.py', timeout=10)
time.sleep(2)
c.exec_command('cd ' + R + ' && source venv/bin/activate && nohup python3 main.py --mode paper > logs/wangcai.log 2>&1 &', timeout=10)
time.sleep(5)
si, so, se = c.exec_command('ps aux | grep "main.py" | grep -v grep', timeout=10)
wc_proc = so.read().decode(errors='ignore').strip()
print('   WangCai:', wc_proc[:200] or 'NOT RUNNING')

# Check web
print('\n5. Verify...')
si, so, se = c.exec_command('curl -s http://localhost:8080/api/status', timeout=10)
status_data = so.read().decode(errors='ignore').strip()
print('   Status API:', status_data)

si, so, se = c.exec_command('curl -s http://localhost:8080/api/module_status', timeout=10)
mod_data = so.read().decode(errors='ignore').strip()
print('   Module API: got', len(mod_data), 'bytes')

si, so, se = c.exec_command('curl -s -o /dev/null -w %{http_code} http://localhost:8080/', timeout=10)
http_code = so.read().decode(errors='ignore').strip()
print('   HTTP code:', http_code)

# Logs
print('\n6. WangCai startup log (last 10 lines):')
si, so, se = c.exec_command('tail -10 ' + R + '/logs/wangcai.log', timeout=10)
print(so.read().decode(errors='ignore').strip() or '(no log)')

print('\n---')
print('✅ Done! http://47.79.86.112:8080')

c.close()
