"""数据库配置和连接"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional, Iterator

import os
from sqlalchemy import create_engine, MetaData, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# 直接从环境变量获取配置
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")
DB_NAME = os.environ.get("DB_NAME", "model_training")
DB_CHARSET = os.environ.get("DB_CHARSET", "utf8mb4")
DB_COLLATION = os.environ.get("DB_COLLATION", "utf8mb4_unicode_ci")
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
DB_POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "30"))
DB_POOL_RECYCLE = int(os.environ.get("DB_POOL_RECYCLE", "3600"))
DB_ECHO = os.environ.get("DB_ECHO", "false").lower() == "true"

# 数据库连接URL
DATABASE_URL = f"mysql+mysqlconnector://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset={DB_CHARSET}"

# 创建数据库引擎
engine = create_engine(
    DATABASE_URL,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT,
    pool_recycle=DB_POOL_RECYCLE,
    echo=DB_ECHO,
    pool_pre_ping=True,
)

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
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "database": DB_NAME,
        "charset": DB_CHARSET,
        "collation": DB_COLLATION,
        "pool_size": DB_POOL_SIZE,
        "max_overflow": DB_MAX_OVERFLOW,
        "pool_timeout": DB_POOL_TIMEOUT,
        "pool_recycle": DB_POOL_RECYCLE,
        "url": DATABASE_URL,
        "password": DB_PASSWORD,  # 直接显示密码
    }