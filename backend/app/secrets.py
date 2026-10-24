"""密码和敏感信息管理"""
from __future__ import annotations

import os
from typing import Optional

class SecretsManager:
    """密码管理器"""
    
    @staticmethod
    def get_db_password() -> Optional[str]:
        """获取数据库密码"""
        # 1. 优先从环境变量获取
        password = os.environ.get("DB_PASSWORD")
        if password:
            return password
        
        # 2. 尝试从Docker secrets文件获取（如果存在）
        try:
            with open("/run/secrets/db_password", "r") as f:
                return f.read().strip()
        except (FileNotFoundError, IOError):
            pass
        
        # 3. 返回None，让调用者决定如何处理
        return None
    
    @staticmethod
    def get_db_user() -> str:
        """获取数据库用户名"""
        return os.environ.get("DB_USER", "root")
    
    @staticmethod
    def get_db_host() -> str:
        """获取数据库主机"""
        return os.environ.get("DB_HOST", "localhost")
    
    @staticmethod
    def get_db_port() -> int:
        """获取数据库端口"""
        return int(os.environ.get("DB_PORT", "3306"))
    
    @staticmethod
    def get_db_name() -> str:
        """获取数据库名称"""
        return os.environ.get("DB_NAME", "model_training")
    
    @staticmethod
    def generate_password(length: int = 16) -> str:
        """生成随机密码"""
        import random
        import string
        
        # 包含小写字母、大写字母、数字和特殊字符
        characters = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(random.choice(characters) for _ in range(length))
    
    @staticmethod
    def create_secrets_file(password: str) -> None:
        """创建Docker secrets文件"""
        try:
            os.makedirs("/run/secrets", exist_ok=True)
            with open("/run/secrets/db_password", "w") as f:
                f.write(password)
            os.chmod("/run/secrets/db_password", 0o600)  # 只有所有者可读写
        except (IOError, OSError) as e:
            print(f"创建secrets文件失败: {e}")


def initialize_secrets() -> None:
    """初始化密码管理"""
    # 检查是否需要生成密码
    if not os.environ.get("DB_PASSWORD"):
        print("警告: DB_PASSWORD未设置，将使用默认密码")
        print("建议在生产环境中设置DB_PASSWORD环境变量")
    
    # 如果存在secrets文件，确保密码正确
    try:
        with open("/run/secrets/db_password", "r") as f:
            stored_password = f.read().strip()
            if stored_password != os.environ.get("DB_PASSWORD"):
                print("警告: 环境变量密码与secrets文件不一致")
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    # 测试密码管理
    print("数据库密码管理测试")
    print(f"用户: {SecretsManager.get_db_user()}")
    print(f"主机: {SecretsManager.get_db_host()}")
    print(f"端口: {SecretsManager.get_db_port()}")
    print(f"数据库: {SecretsManager.get_db_name()}")
    print(f"密码: {'已设置' if SecretsManager.get_db_password() else '未设置'}")
    
    # 生成示例密码
    if not SecretsManager.get_db_password():
        example_password = SecretsManager.generate_password()
        print(f"示例密码: {example_password}")
        print("请将此密码设置为DB_PASSWORD环境变量")