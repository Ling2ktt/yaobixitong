#!/usr/bin/env python3
"""
旺财自动交易系统 - SSH 部署脚本
直接连接阿里云服务器，完成部署
"""

import paramiko
import os
import sys
from pathlib import Path

# ── 服务器配置 ─────────────────────────────
HOST = "47.79.86.112"
PORT = 22
USER = "root"
PASSWORD = "Lxk828221"   # 从用户消息中获取
PROJECT_DIR = "/opt/wangcai"

# 本地项目路径
LOCAL_PROJECT = str(Path(__file__).parent.parent)


def ssh_connect():
    """SSH 连接"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"🔌 连接服务器 {HOST}...")
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=15)
    print("✅ SSH 连接成功")
    return client


def run_cmd(client, cmd, show_output=True):
    """执行命令"""
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=120)
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    if show_output and out.strip():
        print(f"   {out.strip().replace(chr(10), chr(10)+'   ')}")
    if err.strip():
        print(f"   [stderr] {err.strip()}")
    return out, err


def sftp_upload(client, local_path, remote_path):
    """上传文件/目录"""
    sftp = client.open_sftp()
    
    if os.path.isfile(local_path):
        print(f"   📤 上传文件: {os.path.basename(local_path)}")
        sftp.put(local_path, remote_path)
    else:
        # 递归上传目录
        for root, dirs, files in os.walk(local_path):
            rel_root = os.path.relpath(root, local_path)
            remote_root = os.path.join(remote_path, rel_root).replace('\\', '/')
            
            try:
                sftp.stat(remote_root)
            except FileNotFoundError:
                sftp.mkdir(remote_root)
            
            for file in files:
                local_file = os.path.join(root, file)
                rel_file = os.path.relpath(local_file, local_path)
                remote_file = os.path.join(remote_path, rel_file).replace('\\', '/')
                print(f"   📤 {rel_file}")
                sftp.put(local_file, remote_file)
    sftp.close()


def deploy():
    print("\n" + "="*60)
    print("  🐕 旺财自动交易系统 - 服务器部署")
    print("="*60 + "\n")

    client = ssh_connect()

    # ── 1. 停止旧脚本 ───────────────────────
    print("\n[1/6] 停止并删除旧跟单脚本...")
    run_cmd(client, """
        echo "  🔍 查找旧进程..."
        OLD_PIDS=$(ps aux | grep -Ei "(copy|follow|跟单|signal|马丁)" | grep -v grep | awk '{print $2}')
        if [ -n "$OLD_PIDS" ]; then
            echo "  🛑 停止进程: $OLD_PIDS"
            echo "$OLD_PIDS" | xargs -r kill -9 2>/dev/null
        else
            echo "  ℹ️  未找到运行中的旧脚本"
        fi
        
        echo "  🔍 查找旧目录..."
        for dir in /opt/*copy* /opt/*follow* /opt/*trade* /opt/*signal* /root/*copy* /root/*follow* /root/*martin*; do
            if [ -d "$dir" ]; then
                echo "  🗑️  删除: $dir"
                rm -rf "$dir"
            fi
        done
        
        echo "  🔍 检查 crontab..."
        crontab -l 2>/dev/null | grep -v -Ei "(copy|follow|跟单|signal|马丁)" | crontab - 2>/dev/null || true
        echo "✅ 旧脚本清理完成"
    """)

    # ── 2. 创建项目目录 ──────────────────────
    print("\n[2/6] 创建项目目录...")
    run_cmd(client, f"""
        mkdir -p {PROJECT_DIR}
        mkdir -p {PROJECT_DIR}/data
        mkdir -p {PROJECT_DIR}/logs
        mkdir -p {PROJECT_DIR}/reports
        mkdir -p {PROJECT_DIR}/config
        echo "✅ 目录创建完成: {PROJECT_DIR}"
    """)

    # ── 3. 上传代码 ──────────────────────────
    print("\n[3/6] 上传项目代码...")
    print("  （可能需要几分钟，请稍候...）")
    
    # 只上传必要文件，排除 __pycache__, .git 等
    exclude = {'__pycache__', '.git', '.gitignore', 'data', 'logs', 'reports', '__pycache__', '*.pyc', '*.pyo'}
    
    sftp = client.open_sftp()
    
    def upload_dir(local_dir, remote_dir):
        for item in os.listdir(local_dir):
            local_item = os.path.join(local_dir, item)
            remote_item = f"{remote_dir}/{item}".replace('\\', '/')
            
            if os.path.isdir(local_item):
                if item in exclude or item.startswith('.'):
                    continue
                try:
                    sftp.stat(remote_item)
                except FileNotFoundError:
                    sftp.mkdir(remote_item)
                upload_dir(local_item, remote_item)
            else:
                if item.endswith(('.pyc', '.pyo', '.log')):
                    continue
                print(f"   📤 {os.path.relpath(local_item, LOCAL_PROJECT)}")
                sftp.put(local_item, remote_item)
    
    upload_dir(LOCAL_PROJECT, PROJECT_DIR)
    sftp.close()
    print("  ✅ 代码上传完成")

    # ── 4. 安装依赖 ──────────────────────────
    print("\n[4/6] 安装服务器依赖...")
    run_cmd(client, f"""
        set -e
        echo "  🔍 检查 Python..."
        if ! command -v python3 &> /dev/null; then
            echo "  🐳 安装 Python3..."
            apt-get update -qq && apt-get install -y python3 python3-pip python3-venv
        fi
        
        echo "  🐳 检查 Docker..."
        if ! command -v docker &> /dev/null; then
            echo "  🐳 安装 Docker..."
            curl -fsSL https://get.docker.com | bash
            systemctl enable docker
            systemctl start docker
        fi
        
        echo "  🐳 检查 Docker Compose..."
        if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
            echo "  🐳 安装 Docker Compose..."
            curl -L "https://github.com/docker/compose/releases/download/v2.23.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
            chmod +x /usr/local/bin/docker-compose
        fi
        
        echo "  🕐 设置时区..."
        timedatectl set-timezone Asia/Shanghai
        
        echo "✅ 依赖安装完成"
    """, show_output=True)

    # ── 5. 配置 .env ────────────────────────
    print("\n[5/6] 配置环境变量...")
    # 上传 .env 文件（已包含 Binance API Key）
    sftp = client.open_sftp()
    local_env = os.path.join(LOCAL_PROJECT, '.env')
    if os.path.exists(local_env):
        print("  📤 上传 .env 文件...")
        sftp.put(local_env, f"{PROJECT_DIR}/.env")
    else:
        print("  ⚠️  未找到 .env 文件，请手动创建")
    sftp.close()

    # ── 6. 启动服务 ──────────────────────────
    print("\n[6/6] 启动旺财系统...")
    run_cmd(client, f"""
        cd {PROJECT_DIR}
        
        # 用 docker-compose 启动
        if [ -f "docker-compose.yml" ]; then
            echo "  🐳 使用 Docker Compose 启动..."
            docker-compose up -d --build
        else
            echo "  🐍 使用 Python 直接启动（无 Docker）..."
            pip3 install -r requirements.txt -q 2>&1 | tail -3
            nohup python3 main.py --mode paper > logs/wangcai.log 2>&1 &
            echo "  ✅ 旺财已启动（paper 模式）"
            echo "  📋 查看日志: tail -f {PROJECT_DIR}/logs/wangcai.log"
        fi
        
        echo "✅ 启动完成"
    """, show_output=True)

    # ── 7. 验证运行状态 ─────────────────────
    print("\n[7/7] 验证运行状态...")
    run_cmd(client, f"""
        cd {PROJECT_DIR}
        if [ -f "docker-compose.yml" ]; then
            docker-compose ps
        else
            ps aux | grep -E "main.py|wangcai" | grep -v grep
            echo ""
            echo "📋 最新日志（最后 20 行）:"
            tail -20 logs/wangcai.log 2>/dev/null || echo "  日志文件未找到"
        fi
    """, show_output=True)

    client.close()

    # ── 完成信息 ─────────────────────────────
    print("\n" + "="*60)
    print("  🎉 部署完成！")
    print("="*60)
    print(f"\n📋 服务器信息:")
    print(f"  IP: {HOST}")
    print(f"  项目目录: {PROJECT_DIR}")
    print(f"\n🔍 查看运行状态:")
    print(f"  ssh root@{HOST}")
    print(f"  cd {PROJECT_DIR}")
    print(f"  tail -f logs/wangcai.log")
    print(f"\n⚙️  修改配置:")
    print(f"  vim {PROJECT_DIR}/config/system.yaml")
    print(f"\n⚠️  当前为 paper（模拟）模式")
    print(f"  确认无误后，修改 system.yaml 中 sandbox: false 启用实盘\n")


if __name__ == "__main__":
    try:
        deploy()
    except paramiko.AuthenticationException:
        print("\n❌ 认证失败，请检查密码")
        sys.exit(1)
    except paramiko.SSHException as e:
        print(f"\n❌ SSH 错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 部署失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
