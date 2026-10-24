"""MySQL配置示例"""
from __future__ import annotations
from app.mysql_config import MySQLConfig

# 示例1: 使用Docker Compose中的MySQL
docker_config = MySQLConfig(
    host="mysql",
    port=3306,
    user="appuser",
    password="apppass",
    database="model_training"
)

# 示例2: 使用外部MySQL服务器
external_mysql_config = MySQLConfig(
    host="external-mysql.example.com",
    port=3306,
    user="appuser",
    password="secure_password",
    database="model_training",
    pool_size=20,
    max_overflow=30,
    pool_timeout=60,
    pool_recycle=7200,
    echo=False
)

# 示例3: 使用本地MySQL
local_mysql_config = MySQLConfig(
    host="localhost",
    port=3306,
    user="root",
    password="local_password",
    database="model_training"
)

# 示例4: 使用Docker中的MySQL（不同端口）
docker_mysql_config = MySQLConfig(
    host="localhost",
    port=33061,  # 宿主机映射端口
    user="appuser",
    password="apppass",
    database="model_training"
)

# 示例5: 使用SSL连接
ssl_mysql_config = MySQLConfig(
    host="secure-mysql.example.com",
    port=3306,
    user="appuser",
    password="secure_password",
    database="model_training",
    charset="utf8mb4",
    collation="utf8mb4_unicode_ci"
)

# 示例6: 开发环境配置（显示SQL）
dev_mysql_config = MySQLConfig(
    host="localhost",
    port=3306,
    user="root",
    password="dev_password",
    database="model_training_dev",
    echo=True  # 显示SQL语句
)

# 示例7: 生产环境配置
prod_mysql_config = MySQLConfig(
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

# 示例8: 高可用MySQL集群配置
ha_mysql_config = MySQLConfig(
    host="mysql-cluster.example.com",
    port=3306,
    user="appuser",
    password="cluster_password",
    database="model_training",
    pool_size=30,
    max_overflow=50,
    pool_timeout=10,
    pool_recycle=1800,
    echo=False
)

# 示例9: 使用环境变量配置
env_mysql_config = MySQLConfig.from_env()

# 示例10: 自定义配置
custom_mysql_config = MySQLConfig(
    host="custom-mysql.example.com",
    port=3306,
    user="custom_user",
    password="custom_password",
    database="custom_database",
    charset="utf8mb4",
    collation="utf8mb4_unicode_ci",
    pool_size=15,
    max_overflow=25,
    pool_timeout=45,
    pool_recycle=5400,
    echo=False
)

# 使用示例
if __name__ == "__main__":
    # 打印配置信息
    configs = [
        ("Docker Compose", docker_config),
        ("External MySQL", external_mysql_config),
        ("Local MySQL", local_mysql_config),
        ("Docker MySQL", docker_mysql_config),
        ("SSL MySQL", ssl_mysql_config),
        ("Dev MySQL", dev_mysql_config),
        ("Prod MySQL", prod_mysql_config),
        ("HA MySQL", ha_mysql_config),
        ("Env MySQL", env_mysql_config),
        ("Custom MySQL", custom_mysql_config),
    ]
    
    for name, config in configs:
        print(f"\n{name} Configuration:")
        print(f"  Host: {config.host}")
        print(f"  Port: {config.port}")
        print(f"  User: {config.user}")
        print(f"  Database: {config.database}")
        print(f"  URL: {config.get_url()}")