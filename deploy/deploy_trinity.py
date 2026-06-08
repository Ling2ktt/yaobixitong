#!/usr/bin/env python3
"""
妖币系统 - Trinity策略部署脚本
将三位一体(PA+SMC+Wyckoff)策略模块部署到阿里云服务器

用法:
    python deploy/deploy_trinity.py              # 打包文件
    python deploy/deploy_trinity.py --deploy     # 打包 + 部署到云服务器
    python deploy/deploy_trinity.py --restart    # 仅重启云服务器上的妖币系统
"""

import os
import sys
import shutil
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
CLOUD_HOST = "47.79.86.112"
CLOUD_USER = "root"
CLOUD_PATH = "/root/wangcai-trading-bot"
CLOUD_PASSWORD = os.environ.get("ALIYUN_PASSWORD", "")

# 需要部署的文件清单
TRINITY_FILES = [
    # Trinity策略模块
    "modules/trinity_wyckoff.py",
    "modules/trinity_smc.py",
    "modules/trinity_pa.py",
    "modules/trinity_engine.py",
    "modules/trinity_llm_decide.py",
    # 更新后的模块init
    "modules/__init__.py",
    # 更新后的引擎
    "core/engine.py",
    # Trinity配置
    "config/trinity.yaml",
    "config/system.yaml",
    # 测试脚本
    "test_trinity.py",
]

# 策略文档（可选）
DOC_FILES = [
    "docs/price-action-knowledge.md",
    "docs/trinity-strategy.md",
]


def create_deploy_package():
    """创建部署包"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pkg_dir = PROJECT_ROOT / f"deploy_pkg_{timestamp}"
    pkg_dir.mkdir(exist_ok=True)

    for rel_path in TRINITY_FILES:
        src = PROJECT_ROOT / rel_path
        if not src.exists():
            print(f"  ⚠ 文件不存在，跳过: {rel_path}")
            continue
        
        dst = pkg_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  📦 {rel_path}")

    # 创建部署说明
    readme = pkg_dir / "DEPLOY_README.txt"
    with open(readme, "w", encoding="utf-8") as f:
        f.write(f"""=== 妖币系统 Trinity策略部署包 ===
生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

部署步骤:
1. 上传部署包到云服务器:
   scp -r deploy_pkg_{timestamp}/* root@47.79.86.112:/root/wangcai-trading-bot/

2. SSH登录云服务器:
   ssh root@47.79.86.112

3. 重启妖币系统:
   cd /root/wangcai-trading-bot
   pkill -f "main.py"
   sleep 3
   nohup python3 main.py > logs/engine_out.log 2>&1 &

4. 验证Trinity模式:
   curl http://localhost:8081/api/strategy_status
   # 查看logs/wangcai_*.log 确认 "[Engine] 三位一体策略模块已加载"

关键配置变更:
- system.yaml: decision_mode: rule → trinity
- 新增: config/trinity.yaml (LLM审核关闭, 纯代码模式)
- 风险参数: 2%/笔, 5x杠杆, 日亏上限6%
- 信号过滤: B级(≥90分)以上才执行

回滚方案:
- system.yaml: decision_mode 改回 rule
- 或: 删除 modules/trinity_*.py 文件
""")
    print(f"\n  📋 部署说明: {readme}")

    return pkg_dir


def deploy_to_cloud(pkg_dir: Path):
    """部署到云服务器"""
    if not CLOUD_PASSWORD and not shutil.which("sshpass"):
        print("❌ 需要sshpass或设置ALIYUN_PASSWORD环境变量")
        print("   手动部署: scp -r deploy_pkg/ root@47.79.86.112:/root/wangcai-trading-bot/")
        return False

    print(f"\n🚀 部署到 {CLOUD_HOST}:{CLOUD_PATH}")

    # 上传文件
    if shutil.which("sshpass") and CLOUD_PASSWORD:
        cmd = ["sshpass", "-p", CLOUD_PASSWORD, "scp", "-r",
               f"{pkg_dir}/*", f"{CLOUD_USER}@{CLOUD_HOST}:{CLOUD_PATH}/"]
    else:
        cmd = ["scp", "-r", f"{pkg_dir}/*", f"{CLOUD_USER}@{CLOUD_HOST}:{CLOUD_PATH}/"]

    try:
        subprocess.run(cmd, check=True, timeout=60)
        print("  ✅ 文件上传完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ❌ 上传失败: {e}")
        return False
    except subprocess.TimeoutExpired:
        print("  ❌ 上传超时")
        return False


def restart_cloud_service():
    """重启云服务器上的妖币系统"""
    if not CLOUD_PASSWORD and not shutil.which("sshpass"):
        print("❌ 需要sshpass或设置ALIYUN_PASSWORD环境变量")
        return False

    print(f"\n🔄 重启妖币系统 @ {CLOUD_HOST}")

    if shutil.which("sshpass") and CLOUD_PASSWORD:
        ssh_base = ["sshpass", "-p", CLOUD_PASSWORD, "ssh",
                    f"{CLOUD_USER}@{CLOUD_HOST}"]
    else:
        ssh_base = ["ssh", f"{CLOUD_USER}@{CLOUD_HOST}"]

    commands = [
        # 1. 先停止旧进程
        "cd /root/wangcai-trading-bot && pkill -f 'main.py' && sleep 2 && echo '进程已停止'",
        # 2. 确认停止
        "pgrep -f 'main.py' || echo '确认无残留进程'",
        # 3. 检查配置文件
        "cd /root/wangcai-trading-bot && grep decision_mode config/system.yaml",
        # 4. 重启
        "cd /root/wangcai-trading-bot && mkdir -p logs && nohup python3 main.py > logs/engine_out.log 2>&1 & sleep 2 && echo '系统已启动'",
        # 5. 验证
        "sleep 3 && pgrep -f 'main.py' && echo '✅ 进程运行中'",
    ]

    for cmd in commands:
        try:
            result = subprocess.run(
                ssh_base + [cmd],
                capture_output=True, text=True, timeout=30
            )
            print(f"  $ {cmd[:50]}... → {result.stdout.strip()}")
            if result.stderr:
                print(f"    ⚠ {result.stderr.strip()[:100]}")
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            return False

    return True


def verify_deployment():
    """验证部署状态"""
    if not CLOUD_PASSWORD and not shutil.which("sshpass"):
        print("⚠ 无法远程验证，请手动检查")

    print(f"\n📊 验证部署状态...")

    if shutil.which("sshpass") and CLOUD_PASSWORD:
        ssh_base = ["sshpass", "-p", CLOUD_PASSWORD, "ssh",
                    f"{CLOUD_USER}@{CLOUD_HOST}"]
    else:
        ssh_base = ["ssh", f"{CLOUD_USER}@{CLOUD_HOST}"]

    checks = [
        ("Trinity模块文件", "ls /root/wangcai-trading-bot/modules/trinity_*.py | wc -l"),
        ("系统进程", "pgrep -f 'main.py' && echo '运行中' || echo '未运行'"),
        ("决策模式", "grep decision_mode /root/wangcai-trading-bot/config/system.yaml"),
        ("最新日志", "tail -5 /root/wangcai-trading-bot/logs/engine_out.log"),
    ]

    for name, cmd in checks:
        try:
            result = subprocess.run(
                ssh_base + [cmd],
                capture_output=True, text=True, timeout=15
            )
            output = result.stdout.strip()
            print(f"  [{name}] {output[:120]}")
        except Exception as e:
            print(f"  [{name}] ❌ {e}")


def main():
    parser = argparse.ArgumentParser(description="妖币系统 Trinity策略部署")
    parser.add_argument("--deploy", action="store_true", help="打包并部署到云服务器")
    parser.add_argument("--restart", action="store_true", help="仅重启云服务")
    parser.add_argument("--verify", action="store_true", help="验证部署状态")
    args = parser.parse_args()

    os.chdir(PROJECT_ROOT)

    if args.verify:
        verify_deployment()
        return

    if args.restart:
        restart_cloud_service()
        return

    print("=" * 60)
    print("妖币系统 - Trinity 策略部署")
    print("=" * 60)

    # Step 1: 检查文件
    print("\n📋 检查部署文件...")
    missing = [f for f in TRINITY_FILES if not (PROJECT_ROOT / f).exists()]
    if missing:
        print(f"  ❌ 缺少文件: {missing}")
        return

    for f in TRINITY_FILES:
        print(f"  ✅ {f}")
    print(f"  共 {len(TRINITY_FILES)} 个文件就绪")

    # Step 2: 创建部署包
    print("\n📦 创建部署包...")
    pkg_dir = create_deploy_package()

    # Step 3: 部署（如指定）
    if args.deploy:
        deploy_to_cloud(pkg_dir)
        restart_cloud_service()
        verify_deployment()
    else:
        print(f"\n📂 部署包已创建: {pkg_dir}")
        print(f"   手动上传: scp -r {pkg_dir}/* root@{CLOUD_HOST}:{CLOUD_PATH}/")
        print(f"   重启命令: python deploy/deploy_trinity.py --restart")


if __name__ == "__main__":
    main()
