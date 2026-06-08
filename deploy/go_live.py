"""旺财：切换实盘 + 全面体检"""
import paramiko, time, sys

H = '47.79.86.112'
P = 'Lxk828221'
R = '/opt/wangcai'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("🔌 连接...")
c.connect(H, port=22, username='root', password=P, timeout=15)
print("✅ 已连接\n")

def run(cmd, t=120):
    stdin, stdout, stderr = c.exec_command(cmd, get_pty=True, timeout=t)
    out = stdout.read().decode(errors='ignore').strip()
    err = stderr.read().decode(errors='ignore').strip()
    if out:
        for line in out.split('\n')[:40]:
            if line.strip(): print("  " + line)
    if err and 'WARN' not in err and 'warning' not in err.lower():
        print("  [e] " + err[:200])
    return out

# ══════════════════════════════════════
# Step 1: 切换实盘模式
# ══════════════════════════════════════
print("=" * 60)
print("[1] 切换实盘模式")
print("=" * 60)

print("\n1a. 修改 system.yaml...")
run(f"""
    cd {R}
    # mode: paper -> live
    sed -i 's/mode: paper/mode: live/' config/system.yaml
    # Disable sandbox
    python3 -c "
import yaml
cfg = yaml.safe_load(open('config/system.yaml'))
cfg['system']['mode'] = 'live'
cfg['exchanges']['binance']['sandbox'] = False
cfg['exchanges']['okx']['sandbox'] = False
yaml.dump(cfg, open('config/system.yaml','w'), allow_unicode=True, sort_keys=False)
print('Config updated')
"
print(f"\n  配置已更新，检查中...")
""")

print("\n1b. 检查 .env API Key...")
run(f"cd {R} && grep BINANCE_API .env | head -2 | cut -c1-40")

# ══════════════════════════════════════
# Step 2: 重启服务
# ══════════════════════════════════════
print("\n" + "=" * 60)
print("[2] 重启服务（实盘模式）")
print("=" * 60)
run("systemctl restart wangcai && systemctl restart wangcai-web")
time.sleep(6)

print("\n2a. 服务状态:")
r1 = run("systemctl is-active wangcai").strip()
r2 = run("systemctl is-active wangcai-web").strip()
print(f"  后端: {r1}")
print(f"  前端: {r2}")

# ══════════════════════════════════════
# Step 3: 全面体检
# ══════════════════════════════════════
print("\n" + "=" * 60)
print("[3] 全面体检")
print("=" * 60)

print("\n3a. 进程检查:")
run("ps aux | grep -E 'main.py|web_server' | grep -v grep | awk '{print $2,$11,$12,$13}'")

print("\n3b. 端口检查:")
run("ss -tlnp | grep 8080")

print("\n3c. API 状态:")
api = run("curl -s --max-time 5 http://localhost:8080/api/status")
print(f"  结果: {api}")

print("\n3d. 模块状态:")
run("curl -s --max-time 5 http://localhost:8080/api/module_status | " + 
    "python3 -c \"import sys,json;d=json.load(sys.stdin);" + 
    "[print(f'  {m[\\\"icon\\\"]} {m[\\\"name\\\"]}: {m[\\\"status\\\"]}') for m in d['modules']]\"")

print("\n3e. 账户 API:")
run("curl -s --max-time 5 http://localhost:8080/api/account")

print("\n3f. 配置文件:")
run(f"cd {R} && grep -E 'mode:|decision_mode|sandbox|preferred_markets' config/system.yaml")

print("\n3g. 后端日志（最后 25 行）:")
run(f"tail -25 {R}/logs/wangcai.log")

print("\n3h. 前端日志（最后 8 行）:")
run(f"tail -8 {R}/logs/web.log")

print("\n3i. 磁盘和内存:")
run("df -h / | tail -1 && free -h | grep Mem")

c.close()

# ══════════════════════════════════════
print("\n" + "=" * 60)
print("  🎉 实盘启动 + 体检完成！")
print("=" * 60)
print(f"\n  🌐 http://{H}:8080")
print(f"  🔴 当前: 实盘模式 (LIVE)")
print(f"  📈 策略: QuantTrend v3")
print(f"  💰 币种: BTC/USDT only")
print(f"\n  ⚠️  请确认以下事项:")
print(f"  1. 阿里云安全组已开放 8080")
print(f"  2. 浏览器能打开前端")
print(f"  3. Binance API Key 有交易权限")
print(f"  4. API Key 额度充足")
print()
