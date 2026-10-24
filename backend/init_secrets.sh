#!/bin/bash
# 密码初始化脚本

echo "开始初始化密码管理..."

# 检查是否需要生成密码
if [ -z "$DB_PASSWORD" ]; then
    echo "警告: DB_PASSWORD环境变量未设置"
    
    # 尝试从Docker secrets文件读取
    if [ -f "/run/secrets/db_password" ]; then
        echo "从Docker secrets文件读取密码"
        export DB_PASSWORD=$(cat /run/secrets/db_password)
    else
        # 生成随机密码
        echo "生成随机密码..."
        PASSWORD=$(python -c "
import secrets
import string
print(''.join(secrets.choice(string.ascii_letters + string.digits + '!@#$%^&*') for _ in range(16)))
")
        export DB_PASSWORD=$PASSWORD
        
        # 创建secrets文件
        python -c "
from app.secrets import SecretsManager
SecretsManager.create_secrets_file('$PASSWORD')
"
        
        echo "生成的密码: $PASSWORD"
        echo "请将此密码保存到安全的地方，并设置DB_PASSWORD环境变量"
    fi
fi

# 显示配置信息
echo "数据库配置:"
echo "  主机: ${DB_HOST:-localhost}"
echo "  端口: ${DB_PORT:-3306}"
echo "  用户: ${DB_USER:-root}"
echo "  数据库: ${DB_NAME:-model_training}"
echo "  密码: ${DB_PASSWORD:+已设置}${DB_PASSWORD:-未设置}"

# 测试数据库连接
echo "测试数据库连接..."
python -c "
from app.database import test_connection, get_database_info
import sys

if test_connection():
    print('数据库连接成功')
    sys.exit(0)
else:
    print('数据库连接失败')
    print('请检查数据库配置和连接')
    sys.exit(1)
"

if [ $? -eq 0 ]; then
    echo "密码初始化完成"
else
    echo "警告: 数据库连接失败，但应用仍会尝试启动"
fi

echo "密码初始化脚本执行完成"