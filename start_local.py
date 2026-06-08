#!/usr/bin/env python3
"""本地启动旺财系统 - 后端 + 前端"""

import os, sys, time, subprocess, yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(str(ROOT))

# 1. 检查依赖
print("=" * 60)
print("[1/6] 检查依赖...")
for mod in ["ccxt", "loguru", "pandas", "flask", "psutil", "yaml", "numpy"]:
    try:
        __import__(mod)
        print(f"  ✅ {mod}")
    except ImportError:
        print(f"  ❌ {mod} 缺失，安装中...")
        subprocess.run([sys.executable, "-m", "pip", "install", mod, "-q"], check=True)
        print(f"  ✅ {mod} 已安装")

# 2. 检查 .env
print(f"\n[2/6] 检查 .env...")
env_path = ROOT / ".env"
if not env_path.exists():
    print("  ❌ .env 不存在！请先创建：cp .env.example .env")
    sys.exit(1)
with open(env_path, encoding="utf-8") as f:
    env_content = f.read()
has_key = "INroULyguPvgvqCbjVZscidz" in env_content
print(f"  {'✅' if has_key else '⚠️ '} Binance API Key {'已配置' if has_key else '未配置'}")

# 3. 配置决策模式
print(f"\n[3/6] 配置决策模式...")
config_path = ROOT / "config" / "system.yaml"
with open(config_path, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

current = config.get("system", {}).get("decision_mode", "NOT SET")
print(f"  当前: {current}")

# 只在未设置或设为ai(无API Key)时改为rule
if current in ("ai", "NOT SET", None):
    config["system"]["decision_mode"] = "yanchi"
    print(f"  ✅ 已设置为 rule")
else:
    print(f"  ✅ 保持当前模式: {current} (使用 --mode 参数可覆盖)")

with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

# 4. 清理旧进程
print(f"\n[4/6] 清理旧进程...")
import psutil
for p in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        cmd = " ".join(p.info.get("cmdline", [])) if p.info.get("cmdline") else ""
        if "main.py" in cmd or "web_server.py" in cmd:
            p.kill()
            print(f"  🛑 已停止 PID {p.info['pid']}")
    except:
        pass
time.sleep(2)
print("  ✅ 清理完成")

# 5. 启动后端
print(f"\n[5/6] 启动旺财后端 (paper 模式)...")
mkdir_p = lambda d: Path(d).mkdir(parents=True, exist_ok=True)
mkdir_p("logs"); mkdir_p("data"); mkdir_p("reports")

log_file = open("logs/wangcai.log", "a", encoding="utf-8")
backend = subprocess.Popen(
    [sys.executable, "main.py", "--mode", "paper"],
    stdout=log_file, stderr=subprocess.STDOUT,
    cwd=str(ROOT)
)
time.sleep(4)
alive = backend.poll() is None
print(f"  {'✅' if alive else '❌'} 后端 PID: {backend.pid} {'运行中' if alive else '启动失败'}")

# 6. 启动前端
print(f"\n[6/6] 启动 Web 前端 (端口 8080)...")
web_log = open("logs/web.log", "a", encoding="utf-8")
frontend = subprocess.Popen(
    [sys.executable, "web_server.py"],
    stdout=web_log, stderr=subprocess.STDOUT,
    cwd=str(ROOT)
)
time.sleep(3)
alive = frontend.poll() is None
print(f"  {'✅' if alive else '❌'} 前端 PID: {frontend.pid} {'运行中' if alive else '启动失败'}")

# 完成
print(f"\n" + "=" * 60)
print(f"  🎉 旺财系统已启动！")
print(f"=" * 60)
print(f"\n  🌐 前端地址: http://localhost:8080")
print(f"  📋 后端 PID: {backend.pid}")
print(f"  📋 前端 PID: {frontend.pid}")
print(f"\n  查看后端日志: tail -f logs/wangcai.log")
print(f"  查看前端日志: tail -f logs/web.log")
print(f"\n  按 Ctrl+C 停止所有服务\n")

# 等待，让用户看到
try:
    while backend.poll() is None and frontend.poll() is None:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n🛑 正在停止...")
    backend.terminate()
    frontend.terminate()
    print("👋 旺财已停止")
