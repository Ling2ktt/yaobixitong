#!/bin/bash
# ==========================================
# 旺财自动交易系统 - 备份脚本
# ==========================================

set -e

PROJECT_DIR="/opt/wangcai"
BACKUP_DIR="/opt/backups/wangcai"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=30

echo "🐕 旺财系统备份"
echo "==============="

mkdir -p ${BACKUP_DIR}

# 备份数据
echo "[1/3] 备份数据..."
tar czf ${BACKUP_DIR}/data_${DATE}.tar.gz -C ${PROJECT_DIR} data/ logs/ reports/

# 备份配置
echo "[2/3] 备份配置..."
tar czf ${BACKUP_DIR}/config_${DATE}.tar.gz -C ${PROJECT_DIR} config/ .env

# 备份数据库（如果使用PostgreSQL）
if docker ps | grep -q wangcai-postgres; then
    echo "[3/3] 备份数据库..."
    docker exec wangcai-postgres pg_dump -U wangcai wangcai > ${BACKUP_DIR}/db_${DATE}.sql
    gzip ${BACKUP_DIR}/db_${DATE}.sql
fi

# 清理旧备份
echo "清理 ${RETENTION_DAYS} 天前的旧备份..."
find ${BACKUP_DIR} -name "*.tar.gz" -mtime +${RETENTION_DAYS} -delete
find ${BACKUP_DIR} -name "*.sql.gz" -mtime +${RETENTION_DAYS} -delete

echo ""
echo "✅ 备份完成！"
echo "备份位置: ${BACKUP_DIR}"
ls -lh ${BACKUP_DIR}
