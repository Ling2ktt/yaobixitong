#!/bin/bash
# ==========================================
# 旺财自动交易系统 - 阿里云部署脚本（完善版）
# 用法: bash aliyun-deploy.sh
# ==========================================

set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ── 配置（修改这里）─────────────────────────
SERVER_IP="47.79.86.112"
SERVER_USER="root"
SERVER_PORT="22"
PROJECT_DIR="/opt/wangcai"
# ────────────────────────────────────────────

echo -e "${BLUE}"
echo "╔═════════════════════════════════════════════════╗"
echo "║                                                   ║"
echo "║     🐕 旺财自动交易系统 - 部署脚本                ║"
echo "║                                                   ║"
echo "╚═════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── 检查本地文件 ────────────────────────────
echo -e "\n${YELLOW}[0/5] 检查本地文件...${NC}"
if [ ! -f "../.env" ]; then
    echo -e "${RED}❌ 未找到 .env 文件，请先创建：${NC}"
    echo "   cp .env.example .env"
    echo "   并填写 API 密钥"
    exit 1
fi
echo -e "${GREEN}✅ .env 文件存在${NC}"

# ── 输入密码 ────────────────────────────────
echo ""
echo -e "${YELLOW}请输入服务器密码（不会显示）：${NC}"
read -s SERVER_PASS

if [ -z "$SERVER_PASS" ]; then
    echo -e "${RED}❌ 密码不能为空${NC}"
    exit 1
fi

# 检查 sshpass
if ! command -v sshpass &> /dev/null; then
    echo -e "${YELLOW}安装 sshpass...${NC}"
    if command -v apt-get &> /dev/null; then
        sudo apt-get install -y sshpass
    elif command -v yum &> /dev/null; then
        sudo yum install -y sshpass
    else
        echo -e "${RED}请手动安装 sshpass${NC}"
        exit 1
    fi
fi

# ── 1. 测试连接 ────────────────────────────
echo -e "\n${YELLOW}[1/5] 测试服务器连接...${NC}"
if sshpass -p "$SERVER_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
    $SERVER_USER@$SERVER_IP "echo '✅ 连接成功'"; then
    echo -e "${GREEN}✅ 服务器连接正常${NC}"
else
    echo -e "${RED}❌ 连接失败，请检查 IP/密码${NC}"
    exit 1
fi

# ── 2. 停止并删除旧脚本 ──────────────────
echo -e "\n${YELLOW}[2/5] 停止并删除旧跟单脚本...${NC}"
sshpass -p "$SERVER_PASS" ssh -o StrictHostKeyChecking=no $SERVER_USER@$SERVER_IP << 'ENDSSH'
    # 查找并停止跟单相关进程
    echo "  🔍 查找旧脚本进程..."
    OLD_PIDS=$(ps aux | grep -E "(跟单|copy.trade|follow)" | grep -v grep | awk '{print $2}')
    
    if [ -n "$OLD_PIDS" ]; then
        echo "  🛑 停止旧进程: $OLD_PIDS"
        echo "$OLD_PIDS" | xargs kill -9 2>/dev/null || true
    else
        echo "  ℹ️  未找到运行中的旧脚本"
    fi
    
    # 查找常见的跟单脚本目录
    for dir in /opt/*copy* /opt/*follow* /opt/*trade* /root/*copy* /root/*follow*; do
        if [ -d "$dir" ]; then
            echo "  🗑️  删除旧目录: $dir"
            rm -rf "$dir"
        fi
    done
    
    # 检查 crontab
    echo "  🔍 检查 crontab..."
    crontab -l 2>/dev/null | grep -v -E "(copy|follow|跟单)" | crontab - || true
    
    echo "✅ 旧脚本清理完成"
ENDSSH
echo -e "${GREEN}✅ 旧脚本已清理${NC}"

# ── 3. 上传代码 ────────────────────────────
echo -e "\n${YELLOW}[3/5] 上传项目代码...${NC}"
sshpass -p "$SERVER_PASS" ssh -o StrictHostKeyChecking=no $SERVER_USER@$SERVER_IP \
    "mkdir -p $PROJECT_DIR"

echo "  📤 上传代码中..."
sshpass -p "$SERVER_PASS" scp -o StrictHostKeyChecking=no -r \
    ../* $SERVER_USER@$SERVER_IP:$PROJECT_DIR/

echo -e "${GREEN}✅ 代码上传完成${NC}"

# ── 4. 服务器上安装依赖并启动 ───────────
echo -e "\n${YELLOW}[4/5] 服务器环境配置...${NC}"
sshpass -p "$SERVER_PASS" ssh -o StrictHostKeyChecking=no $SERVER_USER@$SERVER_IP << ENDSSH
    set -e
    cd $PROJECT_DIR
    
    # 安装 Docker
    if ! command -v docker &> /dev/null; then
        echo "  🐳 安装 Docker..."
        curl -fsSL https://get.docker.com | bash
        systemctl enable docker
        systemctl start docker
    fi
    
    # 安装 Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        echo "  🐳 安装 Docker Compose..."
        curl -L "https://github.com/docker/compose/releases/download/v2.23.0/docker-compose-\$(uname -s)-\$(uname -m)" \
            -o /usr/local/bin/docker-compose
        chmod +x /usr/local/bin/docker-compose
    fi
    
    # 设置时区
    timedatectl set-timezone Asia/Shanghai
    
    # 创建数据目录
    mkdir -p data logs reports
    
    echo "✅ 环境配置完成"
ENDSSH
echo -e "${GREEN}✅ 服务器环境配置完成${NC}"

# ── 5. 启动服务 ────────────────────────────
echo -e "\n${YELLOW}[5/5] 启动旺财系统...${NC}"
sshpass -p "$SERVER_PASS" ssh -o StrictHostKeyChecking=no $SERVER_USER@$SERVER_IP << 'ENDSSH'
    cd /opt/wangcai
    
    # 用 docker-compose 启动
    docker-compose up -d
    
    echo ""
    echo "✅ 旺财系统已启动！"
    echo ""
    echo "📋 查看日志："
    echo "  docker-compose logs -f"
    echo ""
    echo "🛑 停止服务："
    echo "  docker-compose down"
    echo ""
    echo "📊 查看状态："
    echo "  docker-compose ps"
ENDSSH

echo -e "${GREEN}"
echo "╔═════════════════════════════════════════════════╗"
echo "║                                                   ║"
echo "║            🎉 部署完成！                          ║"
echo "║                                                   ║"
echo "╚═════════════════════════════════════════════════╝"
echo -e "${NC}"
echo ""
echo "📋 下一步："
echo "  1. SSH 登录服务器：ssh root@47.79.86.112"
echo "  2. 查看日志：cd /opt/wangcai && docker-compose logs -f"
echo "  3. 确认信号生成正常后，修改 config/system.yaml 中 sandbox: false"
echo ""
