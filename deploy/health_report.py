"""旺财云服务器深度体检"""
import paramiko, time, json

H = '47.79.86.112'
P = 'Lxk828221'
R = '/opt/wangcai'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(H, port=22, username='root', password=P, timeout=15)

def run(cmd, t=30):
    si, so, se = c.exec_command(cmd, get_pty=True, timeout=t)
    return so.read().decode(errors='ignore').strip(), se.read().decode(errors='ignore').strip()

def check(name, ok, detail=""):
    icon = "✅" if ok else "❌"
    print(f"  {icon} {name}")
    if not ok and detail:
        print(f"     └─ {detail}")
    return ok

results = {"pass": 0, "fail": 0, "warn": 0}

print("=" * 64)
print("  🔍 旺财云服务器·深度全面体检")
print("=" * 64)
print(f"  服务器: {H}")
print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print()

# ═══ 1. 服务状态 ═══
print("━" * 40)
print("【1】systemd 服务状态")
print("━" * 40)
for svc in ['wangcai', 'wangcai-web']:
    out, _ = run(f"systemctl is-active {svc}")
    ok = 'active' in out
    results["pass" if ok else "fail"] += 1
    print(f"  {'✅' if ok else '❌'} {svc}: {out}")

    out, _ = run(f"systemctl is-enabled {svc} 2>/dev/null")
    print(f"     自启: {out.strip()}")

# ═══ 2. 进程 ═══
print("\n" + "━" * 40)
print("【2】进程运行状态")
print("━" * 40)
out, _ = run("ps aux | grep -E 'main.py|web_server' | grep -v grep | awk '{printf \"  PID %-6s CPU %-5s MEM %-5s CMD: %s\\n\", $2, $3\"%\", $4\"%\", $11\" \"$12\" \"$13}'")
ok = 'main.py' in out
results["pass" if ok else "fail"] += 1
print(out if out else "  ❌ 未找到旺财进程")

# ═══ 3. 端口 ═══
print("\n" + "━" * 40)
print("【3】端口监听")
print("━" * 40)
out, _ = run("ss -tlnp | grep 8080")
ok = '8080' in out
results["pass" if ok else "fail"] += 1
print(f"  {'✅ 8080 监听中' if ok else '❌ 8080 未监听'}")
if ok:
    for line in out.split('\n'):
        if '8080' in line:
            print(f"     {line.strip()[:100]}")

# ═══ 4. HTTP API 全量测试 ═══
print("\n" + "━" * 40)
print("【4】HTTP API 端点测试")
print("━" * 40)
apis = [
    ("/api/status", "系统状态"),
    ("/api/module_status", "模块状态"),
    ("/api/account", "账户信息"),
    ("/api/positions", "持仓列表"),
    ("/api/logs?lines=5", "运行日志"),
    ("/api/config", "配置文件"),
]
for path, name in apis:
    out, _ = run(f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 http://localhost:8080{path}")
    ok = out.strip() == '200'
    results["pass" if ok else "fail"] += 1
    content, _ = run(f"curl -s --max-time 5 http://localhost:8080{path}")
    content_preview = content[:120].replace('\n', ' ')
    print(f"  {'✅' if ok else '❌'} {name} (HTTP {out.strip()})")
    if ok:
        print(f"     {content_preview}")

# ═══ 5. 账户余额 ═══
print("\n" + "━" * 40)
print("【5】Binance 账户余额")
print("━" * 40)
content, _ = run("curl -s --max-time 5 http://localhost:8080/api/account")
try:
    data = json.loads(content)
    equity = float(data.get('total_equity', 0))
    avail = float(data.get('available_usdt', 0))
    pos_cnt = data.get('position_count', 0)
    ok = equity > 0
    results["pass" if ok else "fail"] += 1
    print(f"  {'✅' if ok else '❌'} 总权益: ${equity:,.2f}")
    print(f"     可用: ${avail:,.2f}")
    print(f"     持仓: {pos_cnt}")
except:
    print(f"  ❌ JSON 解析失败: {content[:100]}")
    results["fail"] += 1

# ═══ 6. 行情数据 ═══
print("\n" + "━" * 40)
print("【6】BTC 行情数据")
print("━" * 40)
out, _ = run(f"tail -50 {R}/logs/wangcai.log | grep '市场快照生成' | tail -3")
ok = 'BTC/USDT' in out and '均价' in out
results["pass" if ok else "fail"] += 1
print(out if out else "  ❌ 未获取到行情数据")

# ═══ 7. 策略信号 ═══
print("\n" + "━" * 40)
print("【7】QuantTrend 策略信号")
print("━" * 40)
out, _ = run(f"tail -50 {R}/logs/wangcai.log | grep 'Decision.*Rule' | tail -3")
ok = 'Decision' in out
results["pass" if ok else "fail"] += 1
print(out if out else "  ❌ 未获取到策略信号")
out, _ = run(f"tail -100 {R}/logs/wangcai.log | grep '评分:' | tail -1")
score_text = out.strip() if out else ""
if score_text:
    print(f"  {score_text}")

# ═══ 8. 风控模块 ═══
print("\n" + "━" * 40)
print("【8】风控配置")
print("━" * 40)
content, _ = run("curl -s --max-time 5 http://localhost:8080/api/config")
try:
    data = json.loads(content)
    risk = data.get('risk', {})
    system = data.get('system', {})
    print(f"  单笔限额: ${risk.get('max_single_order_usdt', '?')}")
    print(f"  日亏损限: ${risk.get('max_daily_loss_usdt', '?')}")
    print(f"  最大持仓: {risk.get('max_positions', '?')}")
    print(f"  熔断机制: {'启用' if risk.get('circuit_breaker', {}).get('enabled') else '禁用'}")
    print(f"  运行模式: {system.get('mode', '?').upper()}")
    print(f"  决策模式: {system.get('decision_mode', '?')}")
    print(f"  沙盒模式: {'开启' if system.get('sandbox') else '关闭'}")
    results["pass"] += 6
except:
    print("  ❌ 配置读取失败")
    results["fail"] += 6

# ═══ 9. 数据库 ═══
print("\n" + "━" * 40)
print("【9】数据库状态")
print("━" * 40)
out, _ = run(f"cd {R} && source venv/bin/activate && python3 -c \"import sqlite3,os;c=sqlite3.connect('data/wangcai.db');tables=[r[0] for r in c.execute('SELECT name FROM sqlite_master WHERE type=chr(116)+chr(97)+chr(98)+chr(108)+chr(101)')];print('Tables: '+','.join(tables));[print(t+': '+str(c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0])+' rows') for t in tables]\" 2>/dev/null || echo FAIL")
ok = 'Tables' in out
results["pass" if ok else "fail"] += 1
print(out if out else "  ❌ 数据库异常")

# ═══ 10. 系统资源 ═══
print("\n" + "━" * 40)
print("【10】服务器资源")
print("━" * 40)
out, _ = run("free -h | grep Mem | awk '{printf \"  内存: 已用 %s / 总计 %s (%.0f%%)\\n\", $3, $2, ($3/$2)*100}'")
print(out.strip() if out else "  内存: 获取失败")
out, _ = run("df -h / | tail -1 | awk '{printf \"  磁盘: 已用 %s / 总计 %s (%.0f%%)\\n\", $3, $2, ($3/$2)*100}'")
print(out.strip() if out else "  磁盘: 获取失败")
out, _ = run("uptime | awk '{print \"  运行时间: \" $3 \" \" $4}'")
print(out.strip())
out, _ = run("cat /proc/loadavg | awk '{printf \"  负载: %s %s %s\\n\", $1, $2, $3}'")
print(out.strip())

# ═══ 11. 主循环性能 ═══
print("\n" + "━" * 40)
print("【11】主循环性能")
print("━" * 40)
out, _ = run(f"tail -30 {R}/logs/wangcai.log | grep '主循环完成.*耗时' | tail -3")
ok = '耗时' in out
results["pass" if ok else "fail"] += 1
print(out if out else "  未获取到")
out, _ = run(f"tail -100 {R}/logs/wangcai.log | grep '耗时' | tail -3 | grep -oP '耗时: \\d+\\.\\d+s' || echo NONE")
if out.strip() and out.strip() != 'NONE':
    print(f"  {out.strip()}")

# ═══ 12. 错误日志 ═══
print("\n" + "━" * 40)
print("【12】错误与异常")
print("━" * 40)
out, _ = run(f"tail -200 {R}/logs/wangcai.log | grep -c ERROR || echo 0")
err_count = out.strip().split('\n')[-1]
print(f"  最近200行中 ERROR: {err_count} 条")
out, _ = run(f"tail -200 {R}/logs/wangcai.log | grep ERROR | tail -3 | sed 's/^/  /'")
print(out if out else "  无错误")

# ═══ 汇总 ═══
print("\n" + "=" * 64)
total = results["pass"] + results["fail"] + results["warn"]
print(f"  📊 检查项: {total}  |  ✅ {results['pass']}  |  ❌ {results['fail']}  |  ⚠️ {results['warn']}")
print(f"  通过率: {results['pass']/total*100:.0f}%")
print("=" * 64)
print()

if results["fail"] == 0:
    print("  🟢 系统健康 — 实盘运行中")
elif results["fail"] <= 2:
    print("  🟡 有小问题 — 建议处理")
else:
    print("  🔴 需要关注 — 请检查上述失败项")

print(f"\n  🌐 http://{H}:8080")
print()

c.close()
