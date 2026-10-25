"""数据库引擎与会话（配置统一来自 app.config，避免配置双源）。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

import app.config as config

# 数据库连接URL（凭据/池参数全部取自 config.DB_*，单一配置来源）
DATABASE_URL = (
    f"mysql+mysqlconnector://{config.DB_USER}:{config.DB_PASSWORD}"
    f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}?charset={config.DB_CHARSET}"
)

# 创建数据库引擎（惰性连接：首次使用才真正连库，USE_DATABASE=false 不受影响）
engine = create_engine(
    DATABASE_URL,
    pool_size=config.DB_POOL_SIZE,
    max_overflow=config.DB_MAX_OVERFLOW,
    pool_timeout=config.DB_POOL_TIMEOUT,
    pool_recycle=config.DB_POOL_RECYCLE,
    echo=config.DB_ECHO,
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
        "host": config.DB_HOST,
        "port": config.DB_PORT,
        "user": config.DB_USER,
        "database": config.DB_NAME,
        "charset": config.DB_CHARSET,
        "collation": config.DB_COLLATION,
        "pool_size": config.DB_POOL_SIZE,
        "max_overflow": config.DB_MAX_OVERFLOW,
        "pool_timeout": config.DB_POOL_TIMEOUT,
        "pool_recycle": config.DB_POOL_RECYCLE,
        "url": DATABASE_URL,
        "password": config.DB_PASSWORD,  # 直接显示密码
    }
