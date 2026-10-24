# 使用已有MySQL数据库指南

## 概述

本项目已配置为使用已有的MySQL数据库，而不是创建新的MySQL容器。这样可以更好地与现有的基础设施集成，并避免管理额外的数据库服务。

## 前提条件

1. 已有一个可用的MySQL服务器
2. 有足够的权限创建数据库和表
3. 网络连接正常（容器可以访问MySQL服务器）

## 配置步骤

### 1. 复制环境变量文件

```bash
cp .env.example .env
```

### 2. 编辑环境变量

编辑`.env`文件，设置正确的MySQL连接信息：

```bash
# MySQL连接配置
DB_HOST=localhost          # MySQL服务器地址
DB_PORT=3306              # MySQL端口
DB_USER=root              # MySQL用户名
DB_PASSWORD=your_password # MySQL密码
DB_NAME=model_training    # 数据库名称
```

### 3. 创建数据库（如果需要）

如果数据库不存在，请先创建：

```sql
CREATE DATABASE model_training CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 4. 授权用户（如果需要）

为应用用户授权：

```sql
-- 如果使用root用户，可以跳过此步骤
CREATE USER IF NOT EXISTS 'appuser'@'%' IDENTIFIED BY 'apppass';
GRANT ALL PRIVILEGES ON model_training.* TO 'appuser'@'%';
FLUSH PRIVILEGES;
```

### 5. 启动应用

#### 使用GPU版本（推荐）：
```bash
docker compose up -d --build
```

#### 使用CPU版本：
```bash
docker compose -f docker-compose.cpu.yml up -d --build
```

## 高级配置

### 1. 使用远程MySQL服务器

如果MySQL服务器不在本地，修改配置：

```bash
# .env文件
DB_HOST=remote-mysql.example.com
DB_PORT=3306
DB_USER=appuser
DB_PASSWORD=secure_password
DB_NAME=model_training
```

### 2. 使用不同的端口

如果MySQL使用非标准端口：

```bash
# .env文件
DB_PORT=3307  # 使用3307端口
```

### 3. 使用SSL连接（生产环境推荐）

```bash
# .env文件
DB_HOST=secure-mysql.example.com
DB_USER=appuser
DB_PASSWORD=secure_password
DB_NAME=model_training
DB_SSL=true
```

### 4. 调整连接池参数

根据负载调整连接池：

```bash
# .env文件
DB_POOL_SIZE=20      # 增加连接池大小
DB_MAX_OVERFLOW=40    # 增加最大溢出连接数
DB_POOL_TIMEOUT=10   # 减少获取连接超时时间
DB_POOL_RECYCLE=1800 # 减少连接回收时间
```

## 故障排除

### 1. 数据库连接失败

检查以下问题：

- MySQL服务是否运行
- 网络连接是否正常
- 防火墙是否允许连接
- 用户名和密码是否正确
- 数据库是否存在

### 2. 权限错误

确保用户有足够的权限：

```sql
-- 检查权限
SHOW GRANTS FOR 'appuser'@'%';

-- 授予权限
GRANT ALL PRIVILEGES ON model_training.* TO 'appuser'@'%';
FLUSH PRIVILEGES;
```

### 3. 字符集问题

确保数据库使用正确的字符集：

```sql
-- 创建数据库时指定字符集
CREATE DATABASE model_training CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 检查字符集
SHOW VARIABLES LIKE 'character_set_%';
```

### 4. 连接池耗尽

调整连接池参数：

```bash
# .env文件
DB_POOL_SIZE=50      # 增加连接池大小
DB_MAX_OVERFLOW=100  # 增加最大溢出连接数
```

### 5. 容器启动失败

检查日志：

```bash
# 查看容器日志
docker compose logs trainer

# 进入容器调试
docker compose exec trainer bash
```

## 数据库维护

### 1. 备份数据

```bash
# 备份数据库
mysqldump -u root -p model_training > backup.sql

# 恢复数据库
mysql -u root -p model_training < backup.sql
```

### 2. 优化表

```sql
-- 优化表
OPTIMIZE TABLE datasets, training_jobs, job_logs;

-- 分析表
ANALYZE TABLE datasets, training_jobs, job_logs;
```

### 3. 清理过期数据

```sql
-- 清理30天前的日志
DELETE FROM job_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL 30 DAY);
```

## 监控

### 1. 查看数据库状态

```sql
-- 查看数据库状态
SHOW STATUS;

-- 查看慢查询
SHOW VARIABLES LIKE '%slow_query%';

-- 查看连接数
SHOW STATUS LIKE 'Threads_connected';
```

### 2. 应用监控

```bash
# 查看应用日志
docker compose logs trainer

# 查看数据库连接状态
docker compose exec trainer python -c "from app.database import test_connection; print('连接成功' if test_connection() else '连接失败')"
```

## 性能优化

### 1. 索引优化

```sql
-- 为常用查询字段创建索引
CREATE INDEX idx_jobs_status ON training_jobs(status);
CREATE INDEX idx_jobs_created_at ON training_jobs(created_at);
CREATE INDEX idx_datasets_name ON datasets(name);
```

### 2. 查询优化

使用分页查询避免大量数据：

```python
# 分页查询示例
def get_jobs_paginated(page=1, page_size=10):
    offset = (page - 1) * page_size
    return db.query(TrainingJob).offset(offset).limit(page_size).all()
```

### 3. 连接池优化

根据应用负载调整连接池参数：

```bash
# 高负载配置
DB_POOL_SIZE=50
DB_MAX_OVERFLOW=100
DB_POOL_TIMEOUT=10

# 低负载配置
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT=30
```

## 安全建议

### 1. 使用强密码

```bash
# .env文件
DB_PASSWORD=your_strong_password
```

### 2. 限制访问权限

```sql
-- 只授予必要的权限
GRANT SELECT, INSERT, UPDATE, DELETE ON model_training.* TO 'appuser'@'%';
FLUSH PRIVILEGES;
```

### 3. 使用SSL连接

```bash
# .env文件
DB_SSL=true
```

### 4. 定期更新密码

```sql
-- 更新用户密码
ALTER USER 'appuser'@'%' IDENTIFIED BY 'new_password';
FLUSH PRIVILEGES;
```

## 总结

通过使用已有的MySQL数据库，您可以：

1. 更好地集成现有基础设施
2. 避免管理额外的数据库服务
3. 利用现有的备份和监控机制
4. 提高系统的可维护性

请根据您的实际需求调整配置，并定期进行维护和优化。