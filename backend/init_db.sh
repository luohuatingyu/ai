#!/bin/bash
# 数据库初始化：等待 MySQL 就绪（最多 90s）-> 建表；失败则跳过，服务照常启动（数据以文件为主）
echo "等待MySQL服务启动..."

DB_OK=0
for i in $(seq 1 45); do
  if python -c "from app.database import test_connection; import sys; sys.exit(0 if test_connection() else 1)" > /dev/null 2>&1; then
    DB_OK=1
    break
  fi
  echo "MySQL不可用，等待中... ($i/45)"
  sleep 2
done

if [ "$DB_OK" != "1" ]; then
  echo "警告: MySQL 未就绪，跳过数据库初始化（可用 USE_DATABASE=false 彻底关闭数据库路径）"
  exit 0
fi

echo "MySQL服务已启动，正在初始化数据库..."
cd /app
python -m app.db_init || echo "数据库初始化失败，服务将以文件存储为主继续运行"
echo "数据库初始化完成"
