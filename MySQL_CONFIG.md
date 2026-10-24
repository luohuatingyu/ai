# MySQL 配置指南

## 概述

本项目支持多种MySQL配置方式，可以根据不同的部署环境选择合适的配置。

## 配置方式

### 1. 环境变量配置

通过设置环境变量来配置MySQL连接：

```bash
# 基本配置
export DB_HOST=localhost
export DB_PORT=3306
export DB_USER=root
export DB_PASSWORD=password
export DB_NAME=model_training

# 高级配置
export DB_CHARSET=utf8mb4
export DB_COLLATION=utf8mb4_unicode_ci
export DB_POOL_SIZE=10
export DB_MAX_OVERFLOW=20
export DB_POOL_TIMEOUT=30
export DB_POOL_RECYCLE=3600
export DB_ECHO=false
```

### 2. 配置文件配置

创建一个自定义配置文件：

```python
# custom_mysql_config.py
from app.mysql_config import MySQLConfig

my_config = MySQLConfig(
    host="your-mysql-host",
    port=3306,
    user="your-username",
    password="your-password",
    database="your-database",
    pool_size=20,
    max_overflow=30,
    echo=False
)
```

然后在代码中使用：

```python
from app.database import engine, SessionLocal
from custom_mysql_config import my_config

# 使用自定义配置
engine = create_engine(**my_config.get_engine_args())
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

### 3. Docker Compose 配置

在docker-compose.yml中配置MySQL服务：

```yaml
services:
  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: password
      MYSQL_DATABASE: model_training
      MYSQL_USER: appuser
      MYSQL_PASSWORD: apppass
    ports:
      - "3306:3306"  # 宿主机端口:容器端口
    volumes:
      - mysql_data:/var/lib/mysql
    command: --default-authentication-plugin=mysql_native_password
  
  trainer:
    environment:
      DB_HOST: mysql
      DB_PORT: 3306
      DB_USER: appuser
      DB_PASSWORD: apppass
      DB_NAME: model_training
```

## 常见配置场景

### 1. 开发环境

```python
dev_config = MySQLConfig(
    host="localhost",
    port=3306,
    user="root",
    password="dev_password",
    database="model_training_dev",
    echo=True,  # 显示SQL语句
    pool_size=5,
    max_overflow=10
)
```

### 2. 生产环境

```python
prod_config = MySQLConfig(
    host="prod-mysql.example.com",
    port=3306,
    user="appuser",
    password="prod_password",
    database="model_training_prod",
    pool_size=50,
    max_overflow=100,
    pool_timeout=30,
    pool_recycle=3600,
    echo=False
)
```

### 3. Docker 环境

```python
docker_config = MySQLConfig(
    host="mysql",  # Docker服务名
    port=3306,
    user="appuser",
    password="apppass",
    database="model_training"
)
```

### 4. 外部MySQL服务器

```python
external_config = MySQLConfig(
    host="external-mysql.example.com",
    port=3306,
    user="appuser",
    password="secure_password",
    database="model_training",
    pool_size=20,
    max_overflow=30,
    pool_timeout=60,
    pool_recycle=7200
)
```

## 连接测试

### 1. 程序内测试

```python
from app.database import test_connection

if test_connection():
    print("数据库连接成功")
else:
    print("数据库连接失败")
```

### 2. 命令行测试

```bash
# 使用docker-compose测试
docker-compose exec mysql mysql -u appuser -p'apppass' -e "SELECT 1;"

# 直接连接测试
mysql -h localhost -u root -p -e "SELECT 1;"
```

## 故障排除

### 1. 连接失败

检查以下问题：
- MySQL服务是否运行
- 网络连接是否正常
- 用户名和密码是否正确
- 端口是否开放
- 防火墙设置

### 2. 权限错误

确保MySQL用户有足够的权限：

```sql
-- 授予数据库权限
GRANT ALL PRIVILEGES ON model_training.* TO 'appuser'@'%';
FLUSH PRIVILEGES;

-- 检查权限
SHOW GRANTS FOR 'appuser'@'%';
```

### 3. 字符集问题

确保数据库使用正确的字符集：

```sql
-- 创建数据库时指定字符集
CREATE DATABASE model_training CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 检查字符集
SHOW VARIABLES LIKE 'character_set_%';
```

### 4. 连接池问题

调整连接池参数：

```python
config = MySQLConfig(
    host="localhost",
    port=3306,
    user="root",
    password="password",
    database="model_training",
    pool_size=20,      # 连接池大小
    max_overflow=40,   # 最大溢出连接数
    pool_timeout=30,   # 获取连接超时时间
    pool_recycle=3600  # 连接回收时间
)
```

## 性能优化

### 1. 连接池配置

根据应用负载调整连接池参数：

```python
# 高负载配置
high_load_config = MySQLConfig(
    host="localhost",
    port=3306,
    user="root",
    password="password",
    database="model_training",
    pool_size=50,
    max_overflow=100,
    pool_timeout=10,
    pool_recycle=1800
)

# 低负载配置
low_load_config = MySQLConfig(
    host="localhost",
    port=3306,
    user="root",
    password="password",
    database="model_training",
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=3600
)
```

### 2. 索引优化

为常用查询字段创建索引：

```sql
-- 为数据集表创建索引
CREATE INDEX idx_datasets_id ON datasets(id);
CREATE INDEX idx_datasets_name ON datasets(name);

-- 为任务表创建索引
CREATE INDEX idx_jobs_id ON training_jobs(id);
CREATE INDEX idx_jobs_status ON training_jobs(status);
CREATE INDEX idx_jobs_created_at ON training_jobs(created_at);
```

### 3. 查询优化

使用适当的查询语句，避免全表扫描：

```python
# 使用索引查询
def get_jobs_by_status(status: str):
    with get_db() as db:
        return db.query(TrainingJob).filter(TrainingJob.status == status).all()

# 分页查询
def get_jobs_paginated(page: int = 1, page_size: int = 10):
    with get_db() as db:
        offset = (page - 1) * page_size
        return db.query(TrainingJob).offset(offset).limit(page_size).all()
```

## 安全配置

### 1. 密码管理

```python
# 使用环境变量存储密码
import os
from app.mysql_config import MySQLConfig

config = MySQLConfig(
    host="localhost",
    port=3306,
    user="appuser",
    password=os.environ.get("DB_PASSWORD"),  # 从环境变量获取
    database="model_training"
)
```

### 2. SSL连接

```python
ssl_config = MySQLConfig(
    host="secure-mysql.example.com",
    port=3306,
    user="appuser",
    password="secure_password",
    database="model_training",
    charset="utf8mb4",
    collation="utf8mb4_unicode_ci"
)
```

### 3. 防SQL注入

使用SQLAlchemy的参数化查询：

```python
# 安全查询示例
def get_job_by_id(job_id: str):
    with get_db() as db:
        return db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
```

## 备份与恢复

### 1. 数据库备份

```bash
# 使用docker-compose备份
docker-compose exec mysql mysqldump -u appuser -p'apppass' model_training > backup.sql

# 直接备份
mysqldump -u root -p model_training > backup.sql
```

### 2. 数据库恢复

```bash
# 使用docker-compose恢复
docker-compose exec -T mysql mysql -u appuser -p'apppass' model_training < backup.sql

# 直接恢复
mysql -u root -p model_training < backup.sql
```

## 监控与维护

### 1. 查看数据库状态

```sql
-- 查看数据库状态
SHOW STATUS;

-- 查看慢查询
SHOW VARIABLES LIKE '%slow_query%';

-- 查看连接数
SHOW STATUS LIKE 'Threads_connected';
```

### 2. 定期维护

```sql
-- 优化表
OPTIMIZE TABLE datasets, training_jobs, job_logs;

-- 分析表
ANALYZE TABLE datasets, training_jobs, job_logs;

-- 清理过期数据
DELETE FROM job_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL 30 DAY);
```

## 总结

根据不同的部署环境和需求，可以选择合适的MySQL配置方式。关键是要确保：

1. 正确的连接参数
2. 适当的连接池配置
3. 足够的权限
4. 正确的字符集
5. 必要的安全措施
6. 定期的维护和备份

通过合理的配置，可以确保MySQL数据库的稳定性和性能。