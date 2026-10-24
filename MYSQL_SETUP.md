# MySQL 数据库集成说明

## 概述

本项目已集成MySQL数据库支持，用于存储数据集信息和训练任务数据。**项目使用已有的MySQL服务器，不会创建新的MySQL容器**。默认情况下，数据仍然保存到文件系统，但可以选择启用MySQL数据库进行持久化存储。

## 前提条件

1. 已有一个可用的MySQL服务器
2. 有足够的权限创建数据库和表
3. 网络连接正常（容器可以访问MySQL服务器）

## 启动方式

### 1. 配置MySQL连接

```bash
# 复制环境变量文件
cp .env.example .env

# 编辑环境变量，设置正确的MySQL连接信息
nano .env
```

### 2. 创建数据库（如果需要）

```sql
CREATE DATABASE model_training CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 3. 启动应用

#### 使用GPU版本（推荐）
```bash
docker compose up -d --build
```

#### 使用CPU版本
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
- `DB_CHARSET`: 字符集（默认：utf8mb4）
- `DB_COLLATION`: 排序规则（默认：utf8mb4_unicode_ci）
- `DB_POOL_SIZE`: 连接池大小（默认：10）
- `DB_MAX_OVERFLOW`: 最大溢出连接数（默认：20）
- `DB_POOL_TIMEOUT`: 获取连接超时时间（默认：30）
- `DB_POOL_RECYCLE`: 连接回收时间（默认：3600）
- `DB_ECHO`: 是否显示SQL语句（默认：false）

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

## 数据持久化

项目采用双重存储策略：
1. **文件系统**：用于存储模型文件（.pt）、预处理配置等
2. **MySQL数据库**：用于存储元数据、任务状态和日志

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

### 禁用数据库支持
如果不想使用数据库，可以通过环境变量禁用：
```bash
export USE_DATABASE=false
docker compose up -d --build
```

## 性能考虑

1. 数据库操作可能会影响性能，特别是在高并发场景下
2. 日志数据量较大，建议定期清理或使用日志轮转
3. 可以根据需要调整数据库连接池大小
4. 考虑使用读写分离来提高性能

## 备份与恢复

### 数据库备份
```bash
# 备份数据库
mysqldump -u root -p model_training > backup.sql

# 恢复数据库
mysql -u root -p model_training < backup.sql
```

### 文件系统备份
```bash
# 备份整个data目录
tar -czf data_backup.tar.gz data/
```

## 监控与维护

### 查看数据库状态
```sql
-- 查看数据库状态
SHOW STATUS;

-- 查看慢查询
SHOW VARIABLES LIKE '%slow_query%';

-- 查看连接数
SHOW STATUS LIKE 'Threads_connected';
```

### 定期维护
```sql
-- 优化表
OPTIMIZE TABLE datasets, training_jobs, job_logs;

-- 分析表
ANALYZE TABLE datasets, training_jobs, job_logs;

-- 清理过期数据
DELETE FROM job_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL 30 DAY);
```

## 安全建议

1. 使用强密码
2. 限制访问权限
3. 使用SSL连接
4. 定期更新密码
5. 定期备份数据

## 详细配置说明

请参考 [USING_EXISTING_MYSQL.md](./USING_EXISTING_MYSQL.md) 获取更详细的配置和使用说明。