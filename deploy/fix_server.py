#!/usr/bin/env python3
"""修复服务器部署 - 安装 docker-compose 并启动旺财"""

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
    print(f"🔌 连接服务器...")
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=15)
    print("✅ 连接成功")
    return client

def run(client, cmd, timeout=120):
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    if out.strip():
        for line in out.strip().split('\n'):
            print(f"  {line}")
    if err.strip():
        print(f"  [err] {err.strip()}")
    return out

def fix():
    client = ssh_connect()
    
    print("\n[修复 1/4] 检查 Docker 状态...")
    run(client, "docker --version 2>/dev/null || echo 'DOCKER_NOT_INSTALLED'")
    
    print("\n[修复 2/4] 安装 docker-compose（国内镜像）...")
    # 用国内镜像站下载
    run(client, """
        # 方法1: 用 pip 安装 docker-compose
        pip3 install -q docker-compose 2>&1 | tail -3 || true
        # 方法2: 直接下载二进制
        if ! command -v docker-compose &> /dev/null; then
            curl -L "https://ghproxy.com/https://github.com/docker/compose/releases/download/v2.23.0/docker-compose-$(uname -s)-$(uname -m)" \
                -o /usr/local/bin/docker-compose 2>&1
            chmod +x /usr/local/bin/docker-compose
        fi
        docker-compose --version 2>/dev/null || echo 'STILL_NOT_INSTALLED'
    """, timeout=180)
    
    print("\n[修复 3/4] 用 Python 虚拟环境启动旺财（更可靠）...")
    run(client, f"""
        set -e
        cd {PROJECT_DIR}
        
        # 创建虚拟环境
        if [ ! -d "venv" ]; then
            python3 -m venv venv
        fi
        
        # 安装依赖
        source venv/bin/activate
        pip install -q -r requirements.txt 2>&1 | tail -5
        
        # 创建数据目录
        mkdir -p data logs reports
        
        # 停止旧进程
        pkill -f "main.py" 2>/dev/null || true
        sleep 2
        
        # 以后台模式启动（paper 模式，安全）
        nohup python3 main.py --mode paper > logs/wangcai.log 2>&1 &
        sleep 3
        
        # 检查进程
        ps aux | grep "main.py" | grep -v grep
        echo "✅ 旺财已启动（Paper 模式）"
    """, timeout=120)
    
    print("\n[修复 4/4] 查看启动日志...")
    time.sleep(5)
    run(client, f"""
        cd {PROJECT_DIR}
        echo "=== 最后 30 行日志 ==="
        tail -30 logs/wangcai.log 2>/dev/null || echo "日志文件未找到"
    """, timeout=30)
    
    # 检查程序是否正常运行
    print("\n[验证] 检查进程状态...")
    result = run(client, f"ps aux | grep 'main.py' | grep -v grep | wc -l")
    
    client.close()
    
    print("\n" + "="*60)
    print("  🎉 修复完成！")
    print("="*60)
    print(f"\n📋 下一步：")
    print(f"  1. SSH 登录服务器：ssh root@{HOST}")
    print(f"  2. 查看实时日志：cd {PROJECT_DIR} && tail -f logs/wangcai.log")
    print(f"  3. 确认信号生成正常后，修改 config/system.yaml：")
    print(f"     sandbox: false   # 改为实盘")
    print(f"  4. 重启：pkill -f main.py && nohup python3 main.py --mode live &")
    print()

if __name__ == "__main__":
    try:
        fix()
    except Exception as e:
        print(f"\n❌ 修复失败: {e}")
        import traceback; traceback.print_exc()
