# 数据集管理优化

## 项目结构优化

### 新的目录结构

```
data/
├── datasets/
│   ├── raw/              # 原始上传文件
│   ├── processed/        # 处理后的数据集
│   ├── temp/            # 临时文件
│   ├── metadata/        # 元数据存储
│   ├── import_records/  # 导入记录
│   └── [dataset_id]/    # 各数据集的独立目录
├── jobs/                # 训练作业结果
└── ...
```

### 核心模块

1. **DatasetManager** (`dataset_manager.py`)
   - 数据集验证和管理
   - 支持多种数据格式
   - 元数据管理
   - 数据集搜索和过滤

2. **DataProcessor** (`data_processor.py`)
   - 数据预处理功能
   - 支持多种数据处理操作
   - 数据验证和清洗

3. **DatasetImporter** (`dataset_importer.py`)
   - 支持批量导入
   - 多种文件格式支持
   - 压缩文件解压和导入

## 功能特性

### 1. 数据集上传

#### 单个数据集上传
```http
POST /api/datasets
Content-Type: multipart/form-data

file: (CSV文件)
description: (可选) 数据集描述
tags: (可选) 标签列表
```

#### 批量数据集上传
```http
POST /api/datasets/batch-import
Content-Type: multipart/form-data

files: (多个文件)
description: (可选) 批量描述
tags: (可选) 标签列表
```

#### 支持的文件格式
- CSV
- Excel (.xlsx, .xls)
- JSON
- 压缩文件 (.zip, .tar, .tar.gz, .tgz)

### 2. 数据集管理

#### 列出数据集
```http
GET /api/datasets?limit=100&offset=0&tags=[tag1,tag2]&search=keyword
```

#### 获取数据集详情
```http
GET /api/datasets/{ds_id}
```

#### 获取数据集统计信息
```http
GET /api/datasets/{ds_id}/stats
```

#### 获取数据集预览
```http
GET /api/datasets/{ds_id}?rows=10
```

#### 更新数据集描述
```http
POST /api/datasets/{ds_id}/description
Content-Type: application/json

{
  "description": "新的描述"
}
```

#### 管理数据集标签
```http
# 添加标签
POST /api/datasets/{ds_id}/tags
Content-Type: application/json

["tag1", "tag2"]

# 删除标签
DELETE /api/datasets/{ds_id}/tags
Content-Type: application/json

["tag1"]

# 获取标签
GET /api/datasets/{ds_id}/tags

# 获取所有标签
GET /api/datasets/tags
```

#### 删除数据集
```http
DELETE /api/datasets/{ds_id}
```

### 3. 数据预处理

#### 获取数据处理操作
```http
GET /api/processing/operations
```

#### 处理数据集
```http
POST /api/datasets/{ds_id}/process
Content-Type: application/json

{
  "operations": [
    {
      "type": "remove_duplicates",
      "params": {}
    },
    {
      "type": "fill_missing",
      "params": {
        "strategy": "mean",
        "columns": ["列1", "列2"]
      }
    }
  ]
}
```

#### 获取数据概览
```http
GET /api/datasets/{ds_id}/profile
```

### 4. 数据集导入历史

```http
GET /api/datasets/import-history?limit=100
```

### 5. 文件验证

```http
POST /api/datasets/validate
Content-Type: multipart/form-data

file: (要验证的文件)
```

## 数据处理操作

### 1. 去除重复行
```json
{
  "type": "remove_duplicates",
  "params": {
    "subset": ["列1", "列2"],  // 可选，指定列
    "keep": "first"            // 保留策略: "first", "last", false
  }
}
```

### 2. 填充缺失值
```json
{
  "type": "fill_missing",
  "params": {
    "strategy": "mean",        // 策略: "mean", "median", "mode", "forward", "backward", "constant", "drop"
    "columns": ["列1", "列2"], // 可选，指定列
    "value": 0                // 可选，当strategy为constant时使用
  }
}
```

### 3. 数值归一化
```json
{
  "type": "normalize",
  "params": {
    "method": "minmax",       // 方法: "minmax", "zscore"
    "columns": ["列1", "列2"]  // 可选，指定列
  }
}
```

### 4. 分类变量编码
```json
{
  "type": "encode_categorical",
  "params": {
    "method": "onehot",       // 方法: "onehot", "label"
    "columns": ["列1", "列2"]  // 可选，指定列
  }
}
```

### 5. 去除异常值
```json
{
  "type": "remove_outliers",
  "params": {
    "method": "iqr",          // 方法: "iqr", "zscore"
    "columns": ["列1", "列2"], // 可选，指定列
    "factor": 1.5             // 异常值系数
  }
}
```

### 6. 选择列
```json
{
  "type": "select_columns",
  "params": {
    "columns": ["列1", "列2", "列3"]
  }
}
```

### 7. 过滤行
```json
{
  "type": "filter_rows",
  "params": {
    "conditions": [
      {
        "column": "列1",
        "operator": ">",
        "value": 100
      },
      {
        "column": "列2",
        "operator": "contains",
        "value": "关键词"
      }
    ]
  }
}
```

### 8. 列转换
```json
{
  "type": "transform_column",
  "params": {
    "column": "列1",
    "operation": "log",       // 操作: "log", "sqrt", "square", "abs", "round", "datetime", "extract"
    "params": {               // 操作参数
      "decimals": 2           // 当operation为round时使用
    }
  }
}
```

## 数据集元数据

每个数据集包含以下元数据：

```json
{
  "id": "ds-20261024-075705-544b31",
  "name": "数据集名称",
  "description": "数据集描述",
  "tags": ["标签1", "标签2"],
  "rows": 1000,
  "columns": ["列1", "列2", "列3"],
  "column_types": {
    "列1": "int64",
    "列2": "object",
    "列3": "float64"
  },
  "original_filename": "原始文件名.csv",
  "file_size": 1024000,
  "memory_usage": 2048000,
  "created_at": 1698123456.789,
  "processed_at": 1698123456.789,
  "status": "active"
}
```

## 迁移现有数据集

如果需要将现有数据集迁移到新的目录结构，可以运行：

```bash
cd backend
python app/migrate_datasets.py
```

迁移脚本会：
1. 读取现有数据集的元数据
2. 将数据文件复制到新的目录结构
3. 更新元数据
4. 验证迁移结果

## 性能优化

1. **文件存储优化**
   - 原始文件和处理后文件分开存储
   - 使用高效的文件格式
   - 定期清理临时文件

2. **内存管理**
   - 分块处理大数据集
   - 使用适当的数据类型
   - 及时释放内存

3. **索引优化**
   - 为常用查询字段建立索引
   - 使用缓存提高查询性能
   - 定期优化数据库

## 安全考虑

1. **文件上传安全**
   - 验证文件类型和大小
   - 检查文件内容
   - 使用安全的文件名

2. **数据访问控制**
   - 实现用户认证
   - 设置数据访问权限
   - 记录数据访问日志

3. **数据隐私**
   - 敏感数据加密
   - 数据脱敏处理
   - 安全的数据传输

## 扩展功能

1. **数据版本控制**
   - 支持数据集版本管理
   - 版本比较和回滚
   - 变更历史记录

2. **数据共享**
   - 数据集共享功能
   - 协作编辑
   - 权限管理

3. **自动化处理**
   - 数据质量检查
   - 自动化数据清洗
   - 异常检测

4. **集成外部数据源**
   - 数据库连接
   - API数据导入
   - 云存储集成