"""配置单源测试：database.py 的引擎/会话取值必须与 config.DB_* 一致。"""
from __future__ import annotations

from app import config
from app import database


def test_database_values_sourced_from_config():
    """database 模块不再自读 env，全部取自 config.DB_*（防配置双源漂移）。"""
    assert database.DATABASE_URL == (
        f"mysql+mysqlconnector://{config.DB_USER}:{config.DB_PASSWORD}"
        f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}?charset={config.DB_CHARSET}"
    )
    info = database.get_database_info()
    assert info["host"] == config.DB_HOST
    assert info["port"] == config.DB_PORT
    assert info["user"] == config.DB_USER
    assert info["database"] == config.DB_NAME
    assert info["charset"] == config.DB_CHARSET
    assert info["pool_size"] == config.DB_POOL_SIZE


def test_db_available_follows_use_database():
    """DB_AVAILABLE 与 USE_DATABASE 一致（测试环境 false）。"""
    assert config.DB_AVAILABLE == config.USE_DATABASE
    assert config.USE_DATABASE is False  # conftest 固定 false，保证测试不触库


def test_db_engine_lazy_import_safe():
    """database 模块导入不建立真实连接（惰性引擎）。"""
    from sqlalchemy.engine import Engine

    assert isinstance(database.engine, Engine)
    assert database.SessionLocal is not None
