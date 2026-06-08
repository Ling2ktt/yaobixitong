#!/usr/bin/env python3
"""上传前端并启动 Web 服务"""

import paramiko
import time

HOST = "47.79.86.112"
PORT = 22
USER = "root"
PASSWORD = "Lxk828221"
PROJECT_DIR = "/opt/wangcai"
LOCAL_FILE = r"D:\workbuddy\wangcai-trading-bot\web_server.py"

def ssh_connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print("🔌 连接服务器...")
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=20, banner_timeout=30)
    print("✅ SSH 连接成功\n")
    return client

def run(client, cmd, timeout=120):
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='ignore').strip()
    err = stderr.read().decode('utf-8', errors='ignore').strip()
    if out:
        for line in out.split('\n')[:30]:
            if line.strip():
                print(f"  {line}")
    if err and 'WARNING' not in err:
        print(f"  [err] {err[:200]}")
    return out

def main():
    client = ssh_connect()

    # 1. 上传 web_server.py
    print("[1/6] 上传 web_server.py...")
    sftp = client.open_sftp()
    remote_path = f"{PROJECT_DIR}/web_server.py"
    print(f"  本地: {LOCAL_FILE}")
    print(f"  远程: {remote_path}")
    sftp.put(LOCAL_FILE, remote_path)
    sftp.close()
    print("  ✅ 上传完成")

    # 2. 安装 psutil（web_server.py 需要）
    print("\n[2/6] 安装 psutil...")
    run(client, f"""
        cd {PROJECT_DIR}
        source venv/bin/activate
        pip install -q psutil 2>&1 | tail -3
        python3 -c "import psutil; print('psutil', psutil.__version__)"
    """)

    # 3. 停止旧的 web_server（如果有）
    print("\n[3/6] 停止旧的 Web 服务...")
    run(client, """
        pkill -f "web_server.py" 2>/dev/null || true
        sleep 1
        echo "✅ 旧进程已清理"
    """)

    # 4. 以后台模式启动 Web 服务
    print("\n[4/6] 启动 Web 前端服务（端口 8080）...")
    run(client, f"""
        cd {PROJECT_DIR}
        source venv/bin/activate
        nohup python3 web_server.py > logs/web.log 2>&1 &
        WEB_PID=$!
        sleep 3
        echo "  Web 服务 PID: $WEB_PID"
        ps aux | grep 'web_server' | grep -v grep
    """)

    # 5. 确认端口监听
    print("\n[5/6] 确认端口监听...")
    time.sleep(2)
    run(client, """
        netstat -tlnp 2>/dev/null | grep 8080 || ss -tlnp | grep 8080 || echo "端口未监听，检查日志..."
        tail -20 /opt/wangcai/logs/web.log 2>/dev/null || echo "web.log 未找到"
    """)

    # 6. 配置防火墙（开放 8080）
    print("\n[6/6] 配置防火墙（开放 8080 端口）...")
    run(client, """
        # 检查 ufw
        if command -v ufw &> /dev/null; then
            ufw allow 8080/tcp 2>/dev/null || true
            echo "✅ ufw 已开放 8080"
        fi
        # 检查 firewalld
        if command -v firewall-cmd &> /dev/null; then
            firewall-cmd --permanent --add-port=8080/tcp 2>/dev/null && firewall-cmd --reload 2>/dev/null || true
            echo "✅ firewalld 已开放 8080"
        fi
        # 云安全组需要手动在阿里云控制台开放 8080
        echo "⚠️  请在阿里云控制台 → 安全组 → 入方向 开放 8080 端口"
    """)

    client.close()

    print("\n" + "="*60)
    print("  🎉 Web 前端部署完成！")
    print("="*60)
    print(f"\n🌐 访问地址：")
    print(f"  http://{HOST}:8080")
    print(f"\n📋 本地浏览器打开上面的地址即可查看：")
    print(f"  - 系统运行状态")
    print(f"  - 账户权益")
    print(f"  - 当前持仓")
    print(f"  - 运行日志")
    print(f"\n⚠️  重要：请在阿里云控制台开放 8080 端口！")
    print(f"  路径：ECS → 安全组 → 入方向 → 添加规则")
    print(f"  协议：TCP | 端口：8080 | 来源：0.0.0.0/0")
    print()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 失败: {e}")
        import traceback; traceback.print_exc()
