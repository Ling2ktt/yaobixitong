#!/usr/bin/env python3
"""
旺财系统守护进程 (Watchdog)
- 每60秒检查一次核心服务健康状态
- 发现异常自动重启
- 记录守护日志
"""

import subprocess, time, requests, json
from datetime import datetime
from pathlib import Path

LOG = Path("/opt/wangcai/logs/watchdog.log")
API = "http://localhost:8080/api/status"
DEAD_THRESHOLD = 3  # 连续失败3次才重启
CHECK_INTERVAL = 60  # 检测间隔(秒)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def check_health():
    """返回 True=健康, False=异常"""
    try:
        r = requests.get(API, timeout=5)
        data = r.json()
        if data.get("running") and r.status_code == 200:
            # 策略评分检查——连续3次评分为0可能异常
            # 这里只做基本检查
            return True
        log(f"API 返回异常: {data}")
        return False
    except Exception as e:
        log(f"API 不可达: {e}")
        return False

def restart_service(name):
    """重启指定的 systemd 服务"""
    try:
        subprocess.run(["systemctl", "restart", name], capture_output=True, timeout=30)
        time.sleep(5)
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
        ok = "active" in r.stdout
        log(f"重启 {name}: {'✅ 成功' if ok else '❌ 失败'}")
        return ok
    except Exception as e:
        log(f"重启 {name} 失败: {e}")
        return False

log("🛡️ 旺财守护进程启动")
fail_count = 0

while True:
    try:
        if check_health():
            fail_count = 0
        else:
            fail_count += 1
            log(f"异常 #{fail_count}")

        if fail_count >= DEAD_THRESHOLD:
            log(f"连续 {fail_count} 次异常，开始自动恢复...")
            # 先重启前端（web_server），再重启后端（wangcai）
            restart_service("wangcai-web")
            restart_service("wangcai")
            fail_count = 0
            
        time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        log("守护进程收到停止信号")
        break
    except Exception as e:
        log(f"守护进程异常: {e}")
        time.sleep(10)
