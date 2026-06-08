#!/usr/bin/env python3
"""安装 Flask 并重启 Web 服务"""

import paramiko
import time

HOST = "47.79.86.112"
PORT = 22
USER = "root"
PASSWORD = "Lxk828221"
PROJECT_DIR = "/opt/wangcai"

def ssh_connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print("🔌 连接服务器...")
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=20, banner_timeout=30)
    print("✅ 连接成功\n")
    return client

def run(client, cmd, timeout=120):
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='ignore').strip()
    err = stderr.read().decode('utf-8', errors='ignore').strip()
    if out:
        for line in out.split('\n')[:40]:
            if line.strip():
                print(f"  {line}")
    if err and 'WARNING' not in err:
        print(f"  [err] {err[:300]}")
    return out

def main():
    client = ssh_connect()

    # 1. 安装 Flask 到 venv
    print("[1/4] 安装 Flask 到 venv...")
    run(client, f"""
        cd {PROJECT_DIR}
        source venv/bin/activate
        pip install -q flask
        python -c "import flask; print('Flask', flask.__version__)"
    """, timeout=180)

    # 2. 停止旧的 web_server
    print("\n[2/4] 停止旧的 Web 服务...")
    run(client, """
        pkill -f "web_server.py" 2>/dev/null || true
        sleep 2
        echo "✅ 旧进程已停止"
    """)

    # 3. 重新上传最新的 web_server.py（修复语法错误）
    print("\n[3/4] 重新上传 web_server.py...")
    sftp = client.open_sftp()
    local_path = r"D:\workbuddy\wangcai-trading-bot\web_server.py"
    remote_path = f"{PROJECT_DIR}/web_server.py"
    try:
        sftp.put(local_path, remote_path)
        print(f"  ✅ 上传完成: {remote_path}")
    except Exception as e:
        print(f"  ❌ 上传失败: {e}")
    sftp.close()

    # 4. 启动 Web 服务
    print("\n[4/4] 启动 Web 前端服务...")
    run(client, f"""
        cd {PROJECT_DIR}
        source venv/bin/activate
        nohup python web_server.py > logs/web.log 2>&1 &
        WEB_PID=$!
        echo "  Web 服务 PID: $WEB_PID"
        sleep 4
        ps aux | grep 'web_server' | grep -v grep
        echo ""
        echo "  端口监听状态："
        netstat -tlnp 2>/dev/null | grep 8080 || ss -tlnp | grep 8080 || echo "  端口未监听"
    """, timeout=30)

    # 5. 查看启动日志
    print("\n📋 Web 服务启动日志：")
    time.sleep(2)
    run(client, f"tail -30 {PROJECT_DIR}/logs/web.log")

    client.close()

    print("\n" + "="*60)
    print("  🎉 Web 前端部署完成！")
    print("="*60)
    print(f"\n🌐 请在浏览器打开：")
    print(f"  http://{HOST}:8080")
    print(f"\n📋 如果无法访问，请检查：")
    print(f"  1. 阿里云控制台 → 安全组 → 入方向 → 开放 8080 端口")
    print(f"  2. 服务器防火墙：ufw allow 8080/tcp")
    print(f"  3. 查看日志：tail -f {PROJECT_DIR}/logs/web.log")
    print()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 失败: {e}")
        import traceback; traceback.print_exc()
