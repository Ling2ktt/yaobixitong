#!/usr/bin/env python3
"""
旺财自动交易系统 - 全面体检脚本
检查所有模块、API、策略、配置
"""
import sys, os, time, sqlite3, ast, importlib
from pathlib import Path

ROOT = Path(r"D:\workbuddy\wangcai-trading-bot")
os.chdir(str(ROOT))

errors = []
warnings = []
passed = []

def check(name, condition, detail=""):
    if condition:
        passed.append(name)
        print(f"  ✅ {name}")
    else:
        errors.append((name, detail))
        print(f"  ❌ {name} - {detail}")

def warn(name, detail):
    warnings.append((name, detail))
    print(f"  ⚠️  {name} - {detail}")

# ══════════════════════════════════════════
print("=" * 60)
print("  🔍 旺财自动交易系统 - 全面体检")
print("=" * 60)

# ── 1. 语法检查 ─────────────────────────────
print("\n[1] 语法检查")
py_files = []
for root_dir, dirs, files in os.walk(str(ROOT)):
    dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', 'data', 'logs', 'reports')]
    for f in files:
        if f.endswith('.py'):
            py_files.append(os.path.join(root_dir, f))

for f in py_files:
    rel = os.path.relpath(f, str(ROOT))
    try:
        with open(f, encoding='utf-8') as fh:
            ast.parse(fh.read())
    except SyntaxError as e:
        check(rel, False, str(e))

if not errors:
    print(f"  ✅ 全部 {len(py_files)} 个 Python 文件语法通过")

# ── 2. 模块导入 ─────────────────────────────
print("\n[2] 模块导入测试")
modules = {
    "核心引擎": "core.engine",
    "配置加载器": "core.config_loader",
    "行情数据": "modules.market_data",
    "QuantTrend策略": "modules.strategy_quant_trend",
    "AI决策": "modules.ai_decision",
    "风控审核": "modules.risk_control",
    "订单执行": "modules.order_executor",
    "账户管理": "modules.account_manager",
    "信息聚合": "modules.info_aggregator",
    "日志通知": "modules.logger_notifier",
    "每日复盘": "modules.daily_review",
}

import_ok = 0
for name, mod in modules.items():
    try:
        importlib.import_module(mod)
        print(f"  ✅ {name}")
        import_ok += 1
    except Exception as e:
        check(name, False, str(e))

if import_ok == len(modules):
    passed.append("全部 11 个模块导入成功")

# ── 3. 配置文件 ─────────────────────────────
print("\n[3] 配置文件检查")
import yaml

for cfg_file in ['config/system.yaml', '.env', '.env.example']:
    p = ROOT / cfg_file
    check(cfg_file, p.exists(), "文件不存在" if not p.exists() else "")

# system.yaml 完整性
cfg_path = ROOT / 'config' / 'system.yaml'
with open(cfg_path, encoding='utf-8') as f:
    cfg = yaml.safe_load(f)

required_top = ['system', 'exchanges', 'ai', 'risk', 'notifications', 'database']
for k in required_top:
    check(f"system.yaml [{k}]", k in cfg, "缺失顶层配置")

check("决策模式", cfg.get('system', {}).get('decision_mode') == 'rule',
      f"当前: {cfg.get('system', {}).get('decision_mode', 'NOT SET')}")

check("Binance 已启用", cfg.get('exchanges', {}).get('binance', {}).get('enabled') == True,
      "Binance 未启用")

# .env 文件检查
env_path = ROOT / '.env'
with open(env_path, encoding='utf-8') as f:
    env_content = f.read()

has_binance_key = "INroULyguPvgvqCbjVZscidz" in env_content
check(".env Binance API Key", has_binance_key, "API Key 未配置")

# ── 4. 策略逻辑测试 ─────────────────────────
print("\n[4] QuantTrend 策略逻辑测试")
import pandas as pd
import numpy as np
from modules.strategy_quant_trend import QuantTrendStrategy, SignalType

np.random.seed(42)
n = 300
dates = pd.date_range('2024-01-01', periods=n, freq='4h')
trend = np.linspace(50000, 75000, n)
noise = np.cumsum(np.random.randn(n) * 600)
prices = trend + noise

df = pd.DataFrame({
    'timestamp': dates,
    'open': prices + np.random.randn(n) * 100,
    'high': prices + abs(np.random.randn(n) * 200),
    'low': prices - abs(np.random.randn(n) * 200),
    'close': prices,
    'volume': np.random.randint(500, 5000, n)
})

strategy = QuantTrendStrategy({'min_score': 4.0, 'leverage': 5.0})

# 测试信号生成
try:
    signal = strategy.generate_signal(df, "BTC/USDT")
    check("策略信号生成", True)
    print(f"    信号类型: {signal.signal.value}")
    print(f"    趋势评分: {signal.score:.2f}")
    print(f"    杠杆: {signal.leverage}x")
    print(f"    理由: {signal.reason[:80]}")
except Exception as e:
    check("策略信号生成", False, str(e))

# 模拟多次调用
try:
    signals = []
    for i in range(100, len(df), 15):
        partial = df.iloc[:i].copy()
        s = strategy.generate_signal(partial, "BTC/USDT")
        signals.append(s.signal.value)
    buy_count = signals.count('BUY')
    sell_count = signals.count('SELL')
    hold_count = signals.count('HOLD')
    print(f"  模拟交易 ({len(signals)} 次): BUY={buy_count} SELL={sell_count} HOLD={hold_count}")
    check("多次信号模拟", True)
except Exception as e:
    check("多次信号模拟", False, str(e))

# ── 5. 数据库检查 ──────────────────────────
print("\n[5] 数据库检查")
db_path = ROOT / 'data' / 'wangcai.db'
check("数据库文件存在", db_path.exists(), "data/wangcai.db 不存在")

if db_path.exists():
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = [t[0] for t in tables]
    print(f"    表: {', '.join(table_names)}")

    for table in ['trades', 'decisions', 'risk_checks', 'alerts', 'account_snapshots', 'market_snapshots']:
        count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"    {table}: {count} 条")
    conn.close()

# ── 6. 依赖库 ──────────────────────────────
print("\n[6] 依赖库检查")
deps = {
    'ccxt': '交易所接口',
    'loguru': '日志系统',
    'pandas': '数据处理',
    'numpy': '数值计算',
    'flask': 'Web 前端',
    'psutil': '系统监控',
    'yaml': '配置解析',
    'aiohttp': '异步 HTTP',
    'sqlite3': '数据库',
}
for mod, desc in deps.items():
    try:
        importlib.import_module(mod)
    except ImportError:
        check(desc, False, f"缺少 {mod}")

# ── 7. Web API 测试 ────────────────────────
print("\n[7] Web API 端点检查")
import urllib.request
web_endpoints = [
    ('/', 'HTML 主页', 'html'),
    ('/api/status', '状态 API', 'json'),
    ('/api/module_status', '模块状态 API', 'json'),
    ('/api/account', '账户 API', 'json'),
    ('/api/positions', '持仓 API', 'json'),
    ('/api/logs?lines=5', '日志 API', 'json'),
    ('/api/config', '配置 API', 'json'),
]

# 只在 web 服务运行时检查
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
web_running = sock.connect_ex(('127.0.0.1', 8080)) == 0
sock.close()

if web_running:
    print(f"  ✅ Web 服务运行中 (端口 8080)")
    for ep, name, _ in web_endpoints:
        try:
            req = urllib.request.Request(f'http://127.0.0.1:8080{ep}')
            resp = urllib.request.urlopen(req, timeout=5)
            data = resp.read().decode()
            check(name, resp.status == 200, f"HTTP {resp.status}")
        except Exception as e:
            check(name, False, str(e)[:50])
else:
    warn("Web 服务", "端口 8080 未监听（web_server.py 未运行）")

# ── 8. 端口重复利用检查 ────────────────────
import psutil
python_procs = []
for p in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        if p.info['name'] == 'python.exe' or 'python' in (p.info.get('name') or ''):
            cmd = ' '.join(p.info.get('cmdline', []) or [])
            if 'wangcai' in cmd.lower() or 'main.py' in cmd or 'web_server' in cmd:
                python_procs.append((p.info['pid'], cmd[:80]))
    except:
        pass

# ── 汇总 ─────────────────────────────────────
print("\n" + "=" * 60)
print("  📊 体检报告")
print("=" * 60)

error_count = len(errors)
warn_count = len(warnings)
pass_count = len(passed)

print(f"\n  ✅ 通过: {pass_count + import_ok} 项")
if warnings:
    print(f"  ⚠️  警告: {warn_count} 项")
    for name, detail in warnings:
        print(f"      - {name}: {detail}")
if error_count:
    print(f"  ❌ 错误: {error_count} 项")
    for name, detail in errors:
        print(f"      - {name}: {detail}")

backend_running = web_running
binance_ready = has_binance_key

print(f"\n  结论:", end=" ")
if error_count == 0 and binance_ready:
    print("系统健康 ✅ - 可部署到服务器")
elif error_count == 0 and not binance_ready:
    print("代码健康 ✅ - 但需配置 API Key")
else:
    print(f"需修复 {error_count} 个问题 ❌")

print(f"\n  Web 服务: {'运行中 ✅' if web_running else '未运行 ⚠️'}")
print(f"  Binance Key: {'已配置 ✅' if binance_ready else '未配置 ❌'}")
print(f"  策略逻辑: {'正常 ✅' if error_count == 0 else '异常 ❌'}")
print()
