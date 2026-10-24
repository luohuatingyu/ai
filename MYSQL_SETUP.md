# MySQL 数据库集成说明

## 概述

本项目已集成MySQL数据库支持，用于存储数据集信息和训练任务数据。**项目使用已有的MySQL服务器，不会创建新的MySQL容器**。默认情况下，数据仍然保存到文件系统，但可以选择启用MySQL数据库进行持久化存储。

## 配置方式

### 1. 明文密码配置

在 `.env` 文件中直接设置明文密码：

```bash
# .env 文件
DB_HOST=localhost          # MySQL服务器地址
DB_PORT=3306              # MySQL端口
DB_USER=root              # MySQL用户名
DB_PASSWORD=password      # MySQL密码（明文）
DB_NAME=model_training    # 数据库名称
```

### 2. 如果不知道密码，重置MySQL

手动重置MySQL：

```bash
# 检查MySQL连接
mysql -u root -p -e "SELECT 1;"

# 删除并重新创建数据库
mysql -u root -p -e "DROP DATABASE IF EXISTS model_training; CREATE DATABASE model_training CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 创建新用户（可选）
mysql -u root -p -e "CREATE USER IF NOT EXISTS 'appuser'@'%' IDENTIFIED BY 'password'; GRANT ALL PRIVILEGES ON model_training.* TO 'appuser'@'%'; FLUSH PRIVILEGES;"
```

## 启动方式

### 使用GPU版本（推荐）
```bash
docker compose up -d --build
```

### 使用CPU版本
```bash
docker compose -f docker-compose.cpu.yml up -d --build
```

## 数据库配置

项目通过环境变量配置MySQL连接：
- `DB_HOST`: MySQL主机地址（默认：localhost）
- `DB_PORT`: MySQL端口（默认：3306）
- `DB_USER`: MySQL用户名（默认：root）
- `DB_PASSWORD`: MySQL密码（默认：password）
- `DB_NAME`: 数据库名称（默认：model_training）

## 数据库表结构

### datasets 表
存储数据集信息：
- id: 数据集ID（主键）
- name: 数据集名称
- rows: 数据行数
- columns: 列名列表（JSON格式）
- created_at: 创建时间
- updated_at: 更新时间

### training_jobs 表
存储训练任务信息：
- id: 任务ID（主键）
- dataset_id: 数据集ID（外键）
- dataset_name: 数据集名称
- target: 目标列名
- task: 任务类型（classification/regression）
- status: 任务状态（queued/running/completed/failed/cancelled）
- created_at: 创建时间
- started_at: 开始时间
- finished_at: 完成时间
- error: 错误信息
- device: 使用的设备（cpu/gpu）
- params: 训练参数（JSON格式）
- summary: 训练摘要（JSON格式）
- metrics: 训练指标（JSON字符串）

### job_logs 表
存储任务日志：
- id: 日志ID（主键）
- job_id: 任务ID（外键）
- timestamp: 日志时间戳
- message: 日志消息

## 数据库初始化

项目启动时会自动执行数据库初始化：
1. 检查数据库连接
2. 创建所需的表结构（如果不存在）
3. 不会创建数据库，请确保数据库已存在

## 故障排除

### MySQL连接失败
如果出现MySQL连接错误，请检查：
1. MySQL服务是否正常运行
2. 网络连接是否正常
3. 用户名和密码是否正确
4. 数据库是否存在
5. 防火墙是否允许连接

### 数据库表未创建
如果数据库表未创建，可以手动初始化：
```bash
# 进入容器
docker exec -it model-trainer bash

# 运行初始化脚本
python app/db_init.py
```

### 忘记密码
如果忘记密码，可以手动重置MySQL：
```bash
# 删除并重新创建数据库
mysql -u root -p -e "DROP DATABASE IF EXISTS model_training; CREATE DATABASE model_training CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```



## 安全建议

1. 使用强密码
2. 限制访问权限
3. 定期更新密码
4. 定期备份数据

## 详细配置说明

请参考 [USING_EXISTING_MYSQL.md](./USING_EXISTING_MYSQL.md) 获取更详细的配置和使用说明。