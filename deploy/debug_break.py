"""排查旺财中断原因"""
import paramiko, os
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('47.79.86.112', port=22, username='root', password='Lxk828221', timeout=15)
print("✅ 已连接\n")

def run(cmd, timeout=15):
    si, so, se = c.exec_command(cmd, get_pty=True, timeout=timeout)
    return so.read().decode(errors='ignore').strip()

# 1. 主循环时间线——找间隔异常
print("━ 主循环时间线 —")
print(run("grep '主循环.*开始' /opt/wangcai/logs/wangcai.log | awk '{print $1,$2,$8}'"))

# 2. 中断/停止/关闭日志
print("\n━ 中断事件 —")
print(run("grep -iE 'shutdown|停止|exit|killed|signal|OOM|MemoryError|traceback|Error' /opt/wangcai/logs/wangcai.log | tail -15"))

# 3. 系统 journal
print("\n━ systemd journal —")
print(run("journalctl -u wangcai --no-pager -n 20 --since '12 hours ago' | grep -iE 'stop|kill|fail|error|oom|crashed' | tail -10 || echo 'no matches'"))

# 4. 守护进程日志
print("\n━ 守护进程日志 —")
print(run("tail -30 /opt/wangcai/logs/watchdog.log 2>/dev/null || echo 'no watchdog log'"))

# 5. 系统 OOM
print("\n━ OOM 检查 —")
print(run("dmesg | grep -iE 'oom|killed|out of memory' | tail -5 || echo 'no OOM'"))

# 6. 当前状态
print("\n━ 当前状态 —")
print(run("systemctl is-active wangcai wangcai-web wangcai-watchdog"))
print("循环计数:", run("grep -c '主循环.*开始' /opt/wangcai/logs/wangcai.log"))
print("API:", run("curl -s --max-time 5 http://localhost:8080/api/status"))

c.close()
