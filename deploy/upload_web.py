#!/usr/bin/env python3
"""上传新的 web_server.py 并重启 Web 服务"""

import paramiko, time

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
    print("✅ 连接成功\n")
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

    # 1. 上传新的 web_server.py
    print("[1/4] 上传新的 web_server.py...")
    sftp = client.open_sftp()
    remote_path = f"{PROJECT_DIR}/web_server.py"
    sftp.put(LOCAL_FILE, remote_path)
    sftp.close()
    print(f"  ✅ 已上传到 {remote_path}")

    # 2. 检查 flask / psutil 是否已安装
    print("\n[2/4] 确认依赖...")
    run(client, f"""
        cd {PROJECT_DIR}
        source venv/bin/activate
        python3 -c "import flask,psutil;print('flask',flask.__version__,'psutil',psutil.__version__)"
    """)

    # 3. 停止旧的 Web 服务
    print("\n[3/4] 停止旧的 Web 服务...")
    run(client, """
        pkill -f "web_server.py" 2>/dev/null || true
        sleep 2
        ps aux | grep 'web_server' | grep -v grep || echo "  ✅ 已停止"
    """)

    # 4. 启动新的 Web 服务
    print("\n[4/4] 启动 Web 前端服务...")
    run(client, f"""
        cd {PROJECT_DIR}
        source venv/bin/activate
        nohup python3 web_server.py > logs/web.log 2>&1 &
        WEB_PID=$!
        echo "  Web 服务 PID: $WEB_PID"
        sleep 4
        ps aux | grep 'web_server' | grep -v grep
        echo ""
        echo "  端口监听："
        ss -tlnp 2>/dev/null | grep 8080 || netstat -tlnp 2>/dev/null | grep 8080 || echo "  端口未监听，查看日志："
    """, timeout=30)

    # 5. 查看启动日志
    print("\n📋 Web 服务启动日志（最后 20 行）：")
    time.sleep(2)
    run(client, f"tail -20 {PROJECT_DIR}/logs/web.log")

    client.close()

    print("\n" + "=" * 60)
    print("  🎉 Web 前端已更新并重启！")
    print("=" * 60)
    print(f"\n🌐 请在浏览器打开：")
    print(f"  http://{HOST}:8080")
    print(f"\n📋 新功能：")
    print(f"  ✅ 模块级监控（11个模块独立显示状态）")
    print(f"  ✅ 数据概览面板")
    print(f"  ✅ 持仓实时显示")
    print(f"  ✅ 运行日志实时滚动")
    print(f"  ✅ 启动/停止/重启 控制按钮")
    print(f"\n⚠️  如果无法访问，请在阿里云控制台开放 8080 端口")
    print()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 失败: {e}")
        import traceback; traceback.print_exc()
