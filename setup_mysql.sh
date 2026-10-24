#!/bin/bash
# MySQL 快速配置脚本

set -e

echo "=== MySQL 快速配置脚本 ==="
echo ""

# 检查是否在正确的目录
if [ ! -f "docker-compose.yml" ]; then
    echo "错误: 请在项目根目录运行此脚本"
    exit 1
fi

# 生成随机密码
generate_password() {
    python3 -c "
import secrets
import string
print(''.join(secrets.choice(string.ascii_letters + string.digits + '!@#$%^&*') for _ in range(16)))
"
}

# 检查 Python 是否可用
if ! command -v python3 &> /dev/null; then
    echo "错误: 需要 Python 3"
    exit 1
fi

# 生成密码
echo "生成随机密码..."
PASSWORD=$(generate_password)
echo "生成的密码: $PASSWORD"
echo ""

# 创建 .env 文件
if [ ! -f ".env" ]; then
    echo "创建 .env 文件..."
    cat > .env << EOF
# MySQL配置
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=$PASSWORD
DB_NAME=model_training
DB_CHARSET=utf8mb4
DB_COLLATION=utf8mb4_unicode_ci
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=3600
DB_ECHO=false

# 数据持久化目录
DATA_DIR=./data
EOF
    echo ".env 文件已创建"
else
    echo "更新 .env 文件..."
    # 备份原有文件
    cp .env .env.backup
    # 更新密码
    sed -i.bak "s/^DB_PASSWORD=.*/DB_PASSWORD=$PASSWORD/" .env
    rm .env.bak
    echo ".env 文件已更新"
fi

# 创建 secrets 目录（如果需要）
if [ ! -d "secrets" ]; then
    echo "创建 secrets 目录..."
    mkdir -p secrets
    echo "$PASSWORD" > secrets/db_password
    chmod 600 secrets/db_password
    echo "secrets 文件已创建"
fi

# 创建数据目录
if [ ! -d "data" ]; then
    echo "创建数据目录..."
    mkdir -p data
    echo "数据目录已创建"
fi

echo ""
echo "=== 配置完成 ==="
echo ""
echo "请确保以下事项："
echo "1. MySQL 服务器正在运行"
echo "2. 用户 '$(grep DB_USER .env | cut -d'=' -f2)' 有权限访问数据库 '$(grep DB_NAME .env | cut -d'=' -f2)'"
echo "3. 如果数据库不存在，请手动创建："
echo "   CREATE DATABASE $(grep DB_NAME .env | cut -d'=' -f2) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
echo ""
echo "启动应用："
echo "  GPU版本: docker compose up -d --build"
echo "  CPU版本: docker compose -f docker-compose.cpu.yml up -d --build"
echo ""
echo "访问地址: http://localhost:8000"
echo ""
echo "重要提示："
echo "- 请将生成的密码 '$PASSWORD' 保存到安全的地方"
echo "- 不要将密码提交到版本控制系统"
echo "- 定期更换密码以提高安全性"
echo ""
echo "密码管理说明请参考: PASSWORD_MANAGEMENT.md"