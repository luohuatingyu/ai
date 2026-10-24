#!/bin/bash
# 训练平台启动：先做可选的数据库等待/初始化（失败不阻塞训练服务），然后启动 API
bash /app/init_db.sh || echo "警告: 数据库初始化未成功，继续启动（数据以文件存储为主）"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
