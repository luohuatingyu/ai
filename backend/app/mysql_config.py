"""MySQL配置文件"""
from __future__ import annotations
from typing import Optional, Dict, Any
import os

class MySQLConfig:
    """MySQL配置类"""
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        user: str = "root",
        password: str = "password",
        database: str = "model_training",
        charset: str = "utf8mb4",
        collation: str = "utf8mb4_unicode_ci",
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout: int = 30,
        pool_recycle: int = 3600,
        echo: bool = False,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.charset = charset
        self.collation = collation
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.pool_timeout = pool_timeout
        self.pool_recycle = pool_recycle
        self.echo = echo
    
    @classmethod
    def from_env(cls) -> "MySQLConfig":
        """从环境变量创建配置"""
        return cls(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "3306")),
            user=os.environ.get("DB_USER", "root"),
            password=os.environ.get("DB_PASSWORD", "password"),
            database=os.environ.get("DB_NAME", "model_training"),
            charset=os.environ.get("DB_CHARSET", "utf8mb4"),
            collation=os.environ.get("DB_COLLATION", "utf8mb4_unicode_ci"),
            pool_size=int(os.environ.get("DB_POOL_SIZE", "10")),
            max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "20")),
            pool_timeout=int(os.environ.get("DB_POOL_TIMEOUT", "30")),
            pool_recycle=int(os.environ.get("DB_POOL_RECYCLE", "3600")),
            echo=os.environ.get("DB_ECHO", "false").lower() == "true",
        )
    
    def get_url(self) -> str:
        """获取数据库连接URL"""
        return (
            f"mysql+mysqlconnector://{self.user}:{self.password}@"
            f"{self.host}:{self.port}/{self.database}?charset={self.charset}"
        )
    
    def get_engine_args(self) -> Dict[str, Any]:
        """获取SQLAlchemy引擎参数"""
        return {
            "url": self.get_url(),
            "pool_size": self.pool_size,
            "max_overflow": self.max_overflow,
            "pool_timeout": self.pool_timeout,
            "pool_recycle": self.pool_recycle,
            "echo": self.echo,
            "pool_pre_ping": True,
        }

# 默认配置
default_config = MySQLConfig.from_env()

# 获取配置的便捷函数
def get_mysql_config() -> MySQLConfig:
    """获取MySQL配置"""
    return default_config

def get_mysql_url() -> str:
    """获取MySQL连接URL"""
    return default_config.get_url()

def get_mysql_engine_args() -> Dict[str, Any]:
    """获取MySQL引擎参数"""
    return default_config.get_engine_args()