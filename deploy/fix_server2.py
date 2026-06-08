#!/usr/bin/env python3
"""修复服务器 - 简化版（避免超时）"""

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
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=15)
    return client

def run(client, cmd):
    """执行命令（短超时，避免卡死）"""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
    out = stdout.read().decode('utf-8', errors='ignore').strip()
    err = stderr.read().decode('utf-8', errors='ignore').strip()
    if out:
        print(f"  {out[:200]}")
    if err:
        print(f"  [e] {err[:200]}")
    return out

def main():
    print("\n🔌 连接服务器...")
    client = ssh_connect()
    print("✅ 连接成功\n")

    # 1. 修复 pip（用 python3 -m pip 代替 pip3）
    print("[1/5] 修复 pip...")
    run(client, "python3 -m pip --version 2>/dev/null || curl -sS https://bootstrap.pypa.io/get-pip.py | python3 -")
    print("✅ pip 修复完成")

    # 2. 安装 docker-compose（用 pip，比 curl 可靠）
    print("\n[2/5] 安装 docker-compose...")
    run(client, "python3 -m pip install -q docker-compose 2>&1 | tail -3")
    out = run(client, "docker-compose --version 2>/dev/null || which docker-compose || echo NOT_FOUND")
    if "NOT_FOUND" in out:
        print("  ⚠️  docker-compose 未找到，尝试直接下载二进制...")
        run(client, """
            LATEST=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d'\"' -f4)
            curl -L "https://github.com/docker/compose/releases/download/${LATEST}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
            chmod +x /usr/local/bin/docker-compose
            docker-compose --version
        """)
    print("✅ docker-compose 安装完成")

    # 3. 安装 Python 依赖（不用虚拟环境，直接装系统 Python）
    print("\n[3/5] 安装 Python 依赖...")
    run(client, f"cd {PROJECT_DIR} && python3 -m pip install -q -r requirements.txt 2>&1 | tail -5")
    print("✅ Python 依赖安装完成")

    # 4. 停止旧进程（如果有）
    print("\n[4/5] 停止旧进程...")
    run(client, f"cd {PROJECT_DIR} && pkill -f 'python.*main.py' 2>/dev/null || true")
    time.sleep(2)
    print("✅ 旧进程已停止")

    # 5. 启动旺财（paper 模式，后台运行）
    print("\n[5/5] 启动旺财系统（paper 模式）...")
    run(client, f"""
        cd {PROJECT_DIR}
        mkdir -p logs data reports
        nohup python3 main.py --mode paper > logs/wangcai.log 2>&1 &
        sleep 3
        ps aux | grep 'main.py' | grep -v grep
    """)
    print("✅ 旺财已启动")

    # 6. 查看启动日志
    print("\n📋 启动日志（最后 20 行）:")
    run(client, f"cd {PROJECT_DIR} && tail -20 logs/wangcai.log")

    client.close()

    print("\n" + "="*60)
    print("  🎉 部署完成！")
    print("="*60)
    print(f"\n📋 查看实时日志：")
    print(f"  ssh root@{HOST}")
    print(f"  cd {PROJECT_DIR}")
    print(f"  tail -f logs/wangcai.log")
    print(f"\n⚠️  当前为 paper（模拟）模式")
    print(f"  确认信号正常后，修改 config/system.yaml：")
    print(f"    sandbox: false   # 改为实盘")
    print()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 失败: {e}")
        import traceback; traceback.print_exc()
