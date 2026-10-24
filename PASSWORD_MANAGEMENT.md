# 密码管理指南

## 概述

本项目参考了 base-ai 项目的密码管理方案，提供了多种密码配置方式，确保数据库密码的安全管理。

## 密码配置方式

### 1. 环境变量配置（推荐）

通过设置环境变量来配置数据库密码：

```bash
# .env文件
DB_PASSWORD=your_secure_password
```

### 2. Docker Secrets 文件（生产环境推荐）

创建 Docker secrets 文件：

```bash
# 创建 secrets 目录
mkdir -p ./secrets

# 创建密码文件
echo "your_secure_password" > ./secrets/db_password

# 设置文件权限
chmod 600 ./secrets/db_password

# 启动应用时挂载 secrets
docker compose up -d
```

在 docker-compose.yml 中添加：

```yaml
services:
  trainer:
    environment:
      # 其他配置...
    volumes:
      - ./secrets:/run/secrets
```

### 3. 自动生成密码

如果未设置密码，应用会自动生成一个随机密码：

```bash
# 启动应用
docker compose up -d

# 查看生成的密码
docker logs model-trainer | grep "生成的密码"
```

## 密码安全建议

### 1. 密码复杂度要求

- 长度至少 16 位
- 包含大小写字母、数字和特殊字符
- 避免使用常见词汇或个人信息

### 2. 密码轮换

建议定期更换密码：

```bash
# 生成新密码
NEW_PASSWORD=$(python -c "
import secrets
import string
print(''.join(secrets.choice(string.ascii_letters + string.digits + '!@#$%^&*') for _ in range(16)))
")

# 更新环境变量
echo "DB_PASSWORD=$NEW_PASSWORD" >> .env

# 重启应用
docker compose restart
```

### 3. 密码存储

- 不要将密码硬编码在代码中
- 不要将密码提交到版本控制系统
- 使用环境变量或 secrets 文件存储密码

## 配置示例

### 开发环境

```bash
# .env 文件
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=dev_password_123!
DB_NAME=model_training
```

### 生产环境

```bash
# 创建 secrets 文件
echo "prod_strong_password_2024!" > ./secrets/db_password
chmod 600 ./secrets/db_password

# docker-compose.yml
services:
  trainer:
    environment:
      DB_HOST: prod-mysql.example.com
      DB_PORT: 3306
      DB_USER: appuser
      DB_NAME: model_training
    volumes:
      - ./secrets:/run/secrets
```

### Docker Compose Secrets

```yaml
# docker-compose.yml
version: '3.8'

services:
  trainer:
    build: ./backend
    environment:
      DB_HOST: mysql
      DB_PORT: 3306
      DB_USER: appuser
      DB_NAME: model_training
    secrets:
      - db_password
    volumes:
      - ./data:/app/data

secrets:
  db_password:
    file: ./secrets/db_password
```

## 密码管理工具

### 1. 密码生成器

```python
# 生成强密码
import secrets
import string

def generate_password(length=16):
    characters = string.ascii_letters + string.digits + '!@#$%^&*'
    return ''.join(secrets.choice(characters) for _ in range(length))

print(generate_password())
```

### 2. 密码检查器

```python
# 检查密码强度
import re

def check_password_strength(password):
    score = 0
    feedback = []
    
    # 长度检查
    if len(password) >= 16:
        score += 1
    else:
        feedback.append("密码长度至少16位")
    
    # 复杂度检查
    if re.search(r'[a-z]', password):
        score += 1
    else:
        feedback.append("需要包含小写字母")
    
    if re.search(r'[A-Z]', password):
        score += 1
    else:
        feedback.append("需要包含大写字母")
    
    if re.search(r'\d', password):
        score += 1
    else:
        feedback.append("需要包含数字")
    
    if re.search(r'[!@#$%^&*]', password):
        score += 1
    else:
        feedback.append("需要包含特殊字符")
    
    return score, feedback

# 使用示例
password = "YourPassword123!"
score, feedback = check_password_strength(password)
print(f"密码强度评分: {score}/4")
print("改进建议:", feedback)
```

## 故障排除

### 1. 密码错误

```
错误: Access denied for user 'root'@'localhost'
```

解决方案：
- 检查密码是否正确
- 确认用户名和主机名
- 检查MySQL用户权限

### 2. Secrets 文件不存在

```
警告: 无法读取Docker secrets文件
```

解决方案：
- 确认secrets文件存在
- 检查文件权限
- 确认挂载路径正确

### 3. 密码环境变量未设置

```
警告: DB_PASSWORD未设置，将使用默认密码
```

解决方案：
- 设置DB_PASSWORD环境变量
- 或创建secrets文件
- 或使用自动生成的密码

## 最佳实践

### 1. 生产环境

- 使用强密码
- 定期轮换密码
- 使用Docker secrets
- 限制数据库用户权限

### 2. 开发环境

- 使用相对简单的密码
- 不要使用生产密码
- 添加到.gitignore文件

### 3. 测试环境

- 使用测试专用密码
- 考虑使用测试数据库
- 定期清理测试数据

## 密码安全检查清单

- [ ] 密码长度至少16位
- [ ] 包含大小写字母
- [ ] 包含数字
- [ ] 包含特殊字符
- [ ] 避免使用常见词汇
- [ ] 不包含个人信息
- [ ] 定期更换密码
- [ ] 安全存储密码
- [ ] 不提交到版本控制
- [ ] 限制数据库权限
- [ ] 启用SSL连接
- [ ] 监控异常登录

## 总结

通过参考 base-ai 项目的密码管理方案，我们实现了多种密码配置方式，确保了数据库密码的安全性。请根据您的环境选择合适的配置方式，并遵循密码安全最佳实践。