"""数据库初始化脚本（建表）。

mysql 容器镜像由 MYSQL_DATABASE 环境变量自动创建 model_training 库，
因此这里只需要建表（appuser 没有全局 CREATE DATABASE 权限）。
"""
from __future__ import annotations

from app.database import engine, Base

# 导入模型类以完成 Base.metadata 注册
from app.models import (  # noqa: F401
    Dataset,
    JobLog,
    RemoteConfigMigration,
    RemoteServerConfig,
    RemoteSSHCredentialStore,
    TrainingJob,
)


def create_tables() -> None:
    """创建所有数据库表，并为已有日志表补齐游标查询索引。"""
    Base.metadata.create_all(bind=engine)
    log_index = next(
        index for index in JobLog.__table__.indexes
        if index.name == "ix_job_logs_job_id_id"
    )
    log_index.create(bind=engine, checkfirst=True)
    print("数据库表创建成功")


def init_database() -> None:
    """创建表并将已存在的 Redis 远程管理配置幂等迁入 MySQL。"""
    create_tables()
    from app.remote_config_repository import migrate_remote_config_from_redis

    migrate_remote_config_from_redis()
    print("数据库初始化完成")


if __name__ == "__main__":
    init_database()
