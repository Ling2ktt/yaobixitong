#!/bin/bash
# ==========================================
# 旺财自动交易系统 - 更新脚本
# ==========================================

set -e

PROJECT_DIR="/opt/wangcai"

echo "🐕 旺财系统更新"
echo "================"

cd ${PROJECT_DIR}

# 拉取最新代码（如果使用git）
if [ -d ".git" ]; then
    echo "[1/3] 拉取最新代码..."
    git pull origin main
fi

# 重建镜像
echo "[2/3] 重建 Docker 镜像..."
docker-compose build --no-cache

# 重启服务
echo "[3/3] 重启服务..."
docker-compose down
docker-compose up -d

# 显示状态
echo ""
echo "✅ 更新完成！"
echo ""
docker-compose ps
