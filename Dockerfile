# ==========================================
# 旺财自动交易系统 - Docker 镜像
# WangCai Auto Trading System
# ==========================================

FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data /app/logs /app/reports

# 暴露监控端口
EXPOSE 8080 9090

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health')" || exit 1

# 启动命令
CMD ["python", "main.py"]
