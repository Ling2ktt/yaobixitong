#!/usr/bin/env python3
"""旺财全量部署到阿里云服务器"""
import paramiko, time, os

H = '47.79.86.112'
P = 'Lxk828221'
R = '/opt/wangcai'
LOCAL = r'D:\workbuddy\wangcai-trading-bot'

def run(client, cmd, timeout=120):
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=timeout)
    out = stdout.read().decode(errors='ignore').strip()
    err = stderr.read().decode(errors='ignore').strip()
    if out:
        for line in out.split('\n')[:30]:
            if line.strip(): print(f"    {line}")
    if err and 'WARNING' not in err:
        print(f"    [e] {err[:200]}")
    return out

print("=" * 60)
print("  旺财自动交易系统 - 全量部署")
print("=" * 60)

# 1. Connect
print("\n[1/8] SSH 连接...")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(H, port=22, username='root', password=P, timeout=15, banner_timeout=25)
print("  ✅ 已连接")

# 2. Environment setup
print("\n[2/8] 设置服务器环境...")
run(c, """
    # Install Python3 if missing
    if ! command -v python3 &>/dev/null; then
        apt-get update -qq && apt-get install -y python3 python3-pip python3-venv -qq
    fi
    python3 --version

    # Set timezone
    timedatectl set-timezone Asia/Shanghai

    # Create project dir
    mkdir -p /opt/wangcai/{data,logs,reports,config,core,modules,deploy}
    echo "✅ Environment ready"
""", timeout=180)
print("  ✅ 环境就绪")

# 3. Upload all files
print("\n[3/8] 上传项目代码...")
s = c.open_sftp()

# upload files recursively, skip pycache
def upload_dir(local_dir, remote_dir):
    for item in os.listdir(local_dir):
        if item in ('__pycache__', '.git', 'data', 'logs', 'reports'):
            continue
        lp = os.path.join(local_dir, item)
        rp = f"{remote_dir}/{item}".replace('\\', '/')
        if os.path.isdir(lp):
            try: s.stat(rp)
            except FileNotFoundError: s.mkdir(rp)
            upload_dir(lp, rp)
        elif item.endswith(('.py', '.yaml', '.yml', '.md', '.sh', '.txt', '.example', '.service')):
            if os.path.getsize(lp) < 5 * 1024 * 1024:  # < 5MB
                print(f"    📤 {os.path.relpath(lp, LOCAL)}")
                s.put(lp, rp)

upload_dir(LOCAL, R)
# Also copy .env
env_local = os.path.join(LOCAL, '.env')
if os.path.exists(env_local):
    s.put(env_local, f"{R}/.env")
    print("    📤 .env")
s.close()
print("  ✅ 代码上传完成")

# 4. Create venv and install deps
print("\n[4/8] 创建虚拟环境并安装依赖...")
run(c, f"""
    cd {R}
    python3 -m venv venv
    source venv/bin/activate
    pip install -q --upgrade pip 2>&1 | tail -1
    pip install -q -r requirements.txt 2>&1 | tail -5
    echo "✅ Dependencies installed"
    python3 -c "import ccxt,flask,loguru,pandas,numpy,psutil,yaml;print('All imports OK')"
""", timeout=300)
print("  ✅ 依赖安装完成")

# 5. Stop any old processes
print("\n[5/8] 清理旧进程...")
run(c, "pkill -f main.py 2>/dev/null; pkill -f web_server 2>/dev/null; sleep 1; echo '✅'")

# 6. Setup systemd services
print("\n[6/8] 配置 systemd 永久服务...")
run(c, f"""
    # Backend service
    cat > /etc/systemd/system/wangcai.service << 'EOF'
[Unit]
Description=WangCai Trading Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={R}
Environment="PATH={R}/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart={R}/venv/bin/python3 {R}/main.py --mode paper
Restart=always
RestartSec=30
StandardOutput=file:{R}/logs/wangcai.log
StandardError=file:{R}/logs/wangcai_err.log

[Install]
WantedBy=multi-user.target
EOF

    # Frontend service
    cat > /etc/systemd/system/wangcai-web.service << 'EOF'
[Unit]
Description=WangCai Web UI
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={R}
Environment="PATH={R}/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart={R}/venv/bin/python3 {R}/web_server.py
Restart=always
RestartSec=10
StandardOutput=file:{R}/logs/web.log
StandardError=file:{R}/logs/web_err.log

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable wangcai wangcai-web
    echo "✅ systemd services configured"
""")
print("  ✅ systemd 配置完成")

# 7. Start services
print("\n[7/8] 启动旺财系统...")
run(c, """
    systemctl start wangcai
    sleep 4
    systemctl start wangcai-web
    sleep 4
    echo "--- Backend ---"
    systemctl is-active wangcai
    echo "--- Frontend ---"
    systemctl is-active wangcai-web
""")
print("  ✅ 服务已启动")

# 8. Verify
print("\n[8/8] 验证部署...")
run(c, "ps aux | grep -E 'main.py|web_server' | grep -v grep")
run(c, "ss -tlnp | grep 8080")

# Test API
result = run(c, "curl -s --max-time 5 http://localhost:8080/api/status")
print(f"\n  API 状态: {result[:100]}")

# Check logs
print("\n  后端日志:")
run(c, f"tail -10 {R}/logs/wangcai.log")

c.close()

print("\n" + "=" * 60)
print("  🎉 部署完成！")
print("=" * 60)
print(f"\n  🌐 前端地址: http://{H}:8080")
print(f"  📋 管理命令:")
print(f"    systemctl status wangcai wangcai-web")
print(f"    systemctl restart wangcai")
print(f"    journalctl -u wangcai -f")
print(f"    tail -f {R}/logs/wangcai.log")
print(f"\n  ⚠️  阿里云安全组需开放端口: 8080")
print()
