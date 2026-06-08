#!/usr/bin/env python3
"""检查服务器上旺财的运行状态"""

import paramiko

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
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='ignore').strip()
    err = stderr.read().decode('utf-8', errors='ignore').strip()
    if out:
        print(out)
    if err:
        print(f"[err] {err[:200]}")
    return out

client = ssh_connect()
print("✅ 已连接服务器\n")

# 1. 检查进程
print("=" * 60)
print("[1] 检查旺财进程：")
run(client, f"ps aux | grep 'main.py' | grep -v grep")

# 2. 检查 decision_mode
print("\n[2] 检查决策模式：")
run(client, f"grep 'decision_mode' {PROJECT_DIR}/config/system.yaml")

# 3. 检查 sandbox 设置
print("\n[3] 检查 sandbox 设置：")
run(client, f"grep 'sandbox' {PROJECT_DIR}/config/system.yaml")

# 4. 查看最新日志（最后50行）
print("\n[4] 最新运行日志（最后50行）：")
run(client, f"cd {PROJECT_DIR} && tail -50 logs/wangcai.log")

# 5. 检查 .env 里是否有真实 API Key
print("\n[5] 检查 API Key 配置：")
run(client, f"cd {PROJECT_DIR} && grep 'BINANCE_API_KEY' .env | head -c 100")

client.close()
print("\n" + "=" * 60)
print("检查完成！")
