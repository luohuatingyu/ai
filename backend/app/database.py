"""数据库配置和连接"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional, Iterator

from sqlalchemy import create_engine, MetaData, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

from app.mysql_config import get_mysql_config, get_mysql_url, get_mysql_engine_args

# 获取MySQL配置
mysql_config = get_mysql_config()

# 直接使用配置中的密码（明文）
db_password = mysql_config.password

# 数据库连接URL
DATABASE_URL = get_mysql_url()

# 创建数据库引擎
engine = create_engine(**get_mysql_engine_args())

# 创建SessionLocal类
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建Base类
Base = declarative_base()

# 元数据
metadata = MetaData()

# 获取数据库会话的依赖（FastAPI Depends 用）
def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 供 `with db_session() as db:` 使用（同步代码内显式开关会话）
@contextmanager
def db_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 数据库连接测试函数
def test_connection() -> bool:
    """测试数据库连接"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            return True
    except Exception:
        return False

# 获取数据库信息
def get_database_info() -> dict:
    """获取数据库连接信息"""
    return {
        "host": mysql_config.host,
        "port": mysql_config.port,
        "user": mysql_config.user,
        "database": mysql_config.database,
        "charset": mysql_config.charset,
        "collation": mysql_config.collation,
        "pool_size": mysql_config.pool_size,
        "max_overflow": mysql_config.max_overflow,
        "pool_timeout": mysql_config.pool_timeout,
        "pool_recycle": mysql_config.pool_recycle,
        "url": DATABASE_URL,
        "password": db_password,  # 直接显示密码
    }