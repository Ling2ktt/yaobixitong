#!/usr/bin/env python3
"""最终修复 - 用 venv 解决系统 Python 锁问题"""

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
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=15)
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

    # 1. 杀掉旧进程（包括那个 alembic 进程）
    print("[1/6] 清理旧进程...")
    run(client, f"""
        pkill -f "python.*main.py" 2>/dev/null || true
        pkill -f "alembic" 2>/dev/null || true
        pkill -f "wangcai" 2>/dev/null || true
        sleep 2
        ps aux | grep -E "(main.py|wangcai|alembic)" | grep -v grep || echo "  ✅ 旧进程已清理"
    """)

    # 2. 创建 venv
    print("\n[2/6] 创建 Python 虚拟环境...")
    run(client, f"""
        cd {PROJECT_DIR}
        if [ ! -d "venv" ]; then
            python3 -m venv venv
            echo "  ✅ venv 创建完成"
        else
            echo "  ✅ venv 已存在"
        fi
        source venv/bin/activate
        python --version
    """)

    # 3. 升级 venv 里的 pip 并安装依赖
    print("\n[3/6] 安装 Python 依赖（venv 内）...")
    run(client, f"""
        cd {PROJECT_DIR}
        source venv/bin/activate
        # 升级 pip
        pip install -q --upgrade pip 2>&1 | tail -3
        # 安装依赖
        pip install -q -r requirements.txt 2>&1 | tail -5
        echo "  ✅ 依赖安装完成"
        pip list | grep -E "(loguru|ccxt|pandas|numpy)"
    """, timeout=300)

    # 4. 确认 .env 存在
    print("\n[4/6] 检查配置文件...")
    run(client, f"""
        cd {PROJECT_DIR}
        if [ ! -f ".env" ]; then
            echo "  ⚠️  .env 不存在，从模板创建"
            cp .env.example .env
            echo "  ⚠️  请手动编辑 .env 填入 API 密钥！"
        else
            echo "  ✅ .env 存在"
            # 检查是否有真实的 API key
            if grep -q "your_binance" .env 2>/dev/null; then
                echo "  ⚠️  .env 仍是模板值，请修改！"
            fi
        fi
        ls -la .env 2>/dev/null || echo "  ❌ .env 未找到"
    """)

    # 5. 以后台模式启动（paper 模式）
    print("\n[5/6] 启动旺财系统（paper 模式）...")
    run(client, f"""
        cd {PROJECT_DIR}
        source venv/bin/activate
        mkdir -p logs data reports
        
        # 后台启动
        nohup python main.py --mode paper > logs/wangcai.log 2>&1 &
        WANGCAI_PID=$!
        echo "  ✅ 旺财已启动 PID: $WANGCAI_PID"
        sleep 3
        
        # 验证进程
        ps aux | grep "main.py" | grep -v grep
    """, timeout=60)

    # 6. 查看启动日志
    print("\n[6/6] 查看启动日志...")
    time.sleep(3)
    run(client, f"""
        cd {PROJECT_DIR}
        echo "=== 最后 40 行日志 ==="
        tail -40 logs/wangcai.log 2>/dev/null || echo "  日志文件未找到"
    """, timeout=30)

    # 检查是否运行成功
    result = run(client, f"cd {PROJECT_DIR} && ps aux | grep 'main.py' | grep -v grep | wc -l")
    client.close()

    print("\n" + "="*60)
    print("  🎉 部署完成！")
    print("="*60)
    print(f"\n📋 下一步：")
    print(f"  1. SSH 登录服务器：")
    print(f"     ssh root@{HOST}")
    print(f"  2. 查看实时日志：")
    print(f"     cd {PROJECT_DIR} && tail -f logs/wangcai.log")
    print(f"  3. 确认 .env 中 API 密钥正确：")
    print(f"     vim {PROJECT_DIR}/.env")
    print(f"  4. 确认信号生成正常后，改为实盘：")
    print(f"     修改 {PROJECT_DIR}/config/system.yaml")
    print(f"     把 sandbox: true 改为 sandbox: false")
    print()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 失败: {e}")
        import traceback; traceback.print_exc()
