#!/bin/bash
# MySQL 管理脚本

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 配置
DB_HOST="localhost"
DB_PORT="3306"
DB_USER="root"
DB_NAME="model_training"

# 函数：打印带颜色的消息
print_message() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# 函数：检查MySQL连接
check_mysql_connection() {
    print_message $YELLOW "检查MySQL连接..."
    
    if mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -e "SELECT 1;" &>/dev/null; then
        print_message $GREEN "MySQL连接成功"
        return 0
    else
        print_message $RED "MySQL连接失败"
        return 1
    fi
}

# 函数：删除数据库
delete_database() {
    print_message $YELLOW "删除数据库 $DB_NAME..."
    
    if mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -e "DROP DATABASE IF EXISTS $DB_NAME;" 2>/dev/null; then
        print_message $GREEN "数据库 $DB_NAME 删除成功"
    else
        print_message $RED "删除数据库失败"
        exit 1
    fi
}

# 函数：创建数据库
create_database() {
    print_message $YELLOW "创建数据库 $DB_NAME..."
    
    if mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -e "CREATE DATABASE $DB_NAME CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null; then
        print_message $GREEN "数据库 $DB_NAME 创建成功"
    else
        print_message $RED "创建数据库失败"
        exit 1
    fi
}

# 函数：创建用户
create_user() {
    local username="$1"
    local password="$2"
    
    print_message $YELLOW "创建用户 $username..."
    
    # 删除用户（如果存在）
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -e "DROP USER IF EXISTS '$username'@'%';" 2>/dev/null
    
    # 创建用户
    if mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -e "CREATE USER '$username'@'%' IDENTIFIED BY '$password';" 2>/dev/null; then
        print_message $GREEN "用户 $username 创建成功"
    else
        print_message $RED "创建用户失败"
        exit 1
    fi
    
    # 授权
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -e "GRANT ALL PRIVILEGES ON $DB_NAME.* TO '$username'@'%';" 2>/dev/null
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -e "FLUSH PRIVILEGES;" 2>/dev/null
    print_message $GREEN "用户 $username 授权成功"
}

# 函数：重置密码
reset_password() {
    local username="$1"
    local password="$2"
    
    print_message $YELLOW "重置用户 $username 的密码..."
    
    if mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -e "ALTER USER '$username'@'%' IDENTIFIED BY '$password';" 2>/dev/null; then
        print_message $GREEN "密码重置成功"
    else
        print_message $RED "密码重置失败"
        exit 1
    fi
}

# 函数：显示帮助
show_help() {
    echo "MySQL 管理脚本"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  check              检查MySQL连接"
    echo "  delete             删除数据库 $DB_NAME"
    echo "  create             创建数据库 $DB_NAME"
    echo "  reset              删除并重新创建数据库 $DB_NAME"
    echo "  user <username>    创建用户"
    echo "  pass <username>    重置用户密码"
    echo "  info               显示数据库信息"
    echo "  help               显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 reset           # 删除并重新创建数据库"
    echo "  $0 user appuser    # 创建用户"
    echo "  $0 pass appuser    # 重置用户密码"
}

# 主函数
main() {
    case "${1:-}" in
        "check")
            check_mysql_connection
            ;;
        "delete")
            check_mysql_connection
            delete_database
            ;;
        "create")
            check_mysql_connection
            create_database
            ;;
        "reset")
            check_mysql_connection
            delete_database
            create_database
            print_message $GREEN "数据库重置完成"
            ;;
        "user")
            if [ -z "$2" ]; then
                print_message $RED "错误: 请指定用户名"
                exit 1
            fi
            check_mysql_connection
            create_user "$2" "password123"  # 默认密码
            print_message $YELLOW "请记住密码: password123"
            ;;
        "pass")
            if [ -z "$2" ]; then
                print_message $RED "错误: 请指定用户名"
                exit 1
            fi
            check_mysql_connection
            reset_password "$2" "password123"  # 默认密码
            print_message $YELLOW "新密码: password123"
            ;;
        "info")
            check_mysql_connection
            echo "数据库信息:"
            echo "  主机: $DB_HOST"
            echo "  端口: $DB_PORT"
            echo "  用户: $DB_USER"
            echo "  数据库: $DB_NAME"
            ;;
        "help"|"-h"|"--help")
            show_help
            ;;
        "")
            print_message $RED "错误: 请指定操作"
            echo ""
            show_help
            exit 1
            ;;
        *)
            print_message $RED "错误: 未知操作 '$1'"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# 运行主函数
main "$@"