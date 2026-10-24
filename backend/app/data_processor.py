"""数据预处理模块，提供数据清洗、转换等功能"""
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)

class DataProcessor:
    """数据处理器，提供数据清洗和转换功能"""
    
    def __init__(self):
        self.supported_operations = {
            "remove_duplicates": self.remove_duplicates,
            "fill_missing": self.fill_missing,
            "normalize": self.normalize,
            "encode_categorical": self.encode_categorical,
            "remove_outliers": self.remove_outliers,
            "select_columns": self.select_columns,
            "filter_rows": self.filter_rows,
            "transform_column": self.transform_column,
        }
    
    def process_dataset(self, df: pd.DataFrame, operations: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        处理数据集
        
        Args:
            df: 原始数据框
            operations: 操作列表，每个操作是一个字典，包含操作类型和参数
            
        Returns:
            DataFrame: 处理后的数据框
        """
        processed_df = df.copy()
        
        for op in operations:
            op_type = op.get("type")
            if op_type not in self.supported_operations:
                raise HTTPException(400, f"不支持的操作类型: {op_type}")
            
            try:
                processed_df = self.supported_operations[op_type](processed_df, op.get("params", {}))
                logger.info(f"成功执行操作: {op_type}")
            except Exception as e:
                logger.error(f"执行操作 {op_type} 失败: {e}")
                raise HTTPException(400, f"执行操作 {op_type} 失败: {e}")
        
        return processed_df
    
    def remove_duplicates(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        """去除重复行"""
        subset = params.get("subset", None)
        keep = params.get("keep", "first")
        
        return df.drop_duplicates(subset=subset, keep=keep)
    
    def fill_missing(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        """填充缺失值"""
        strategy = params.get("strategy", "mean")
        columns = params.get("columns", df.columns.tolist())
        
        result_df = df.copy()
        
        for col in columns:
            if col not in df.columns:
                continue
                
            if strategy == "mean":
                if df[col].dtype in ['int64', 'float64']:
                    result_df[col] = df[col].fillna(df[col].mean())
            elif strategy == "median":
                if df[col].dtype in ['int64', 'float64']:
                    result_df[col] = df[col].fillna(df[col].median())
            elif strategy == "mode":
                mode_value = df[col].mode()
                if len(mode_value) > 0:
                    result_df[col] = df[col].fillna(mode_value[0])
            elif strategy == "forward":
                result_df[col] = df[col].fillna(method='ffill')
            elif strategy == "backward":
                result_df[col] = df[col].fillna(method='bfill')
            elif strategy == "constant":
                fill_value = params.get("value", 0)
                result_df[col] = df[col].fillna(fill_value)
            elif strategy == "drop":
                result_df = result_df.dropna(subset=[col])
        
        return result_df
    
    def normalize(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        """数值列归一化"""
        method = params.get("method", "minmax")
        columns = params.get("columns", df.select_dtypes(include=[np.number]).columns.tolist())
        
        result_df = df.copy()
        
        for col in columns:
            if col not in df.columns or df[col].dtype not in ['int64', 'float64']:
                continue
                
            if method == "minmax":
                min_val = df[col].min()
                max_val = df[col].max()
                if max_val != min_val:
                    result_df[col] = (df[col] - min_val) / (max_val - min_val)
            elif method == "zscore":
                mean_val = df[col].mean()
                std_val = df[col].std()
                if std_val != 0:
                    result_df[col] = (df[col] - mean_val) / std_val
        
        return result_df
    
    def encode_categorical(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        """分类变量编码"""
        method = params.get("method", "onehot")
        columns = params.get("columns", df.select_dtypes(include=['object']).columns.tolist())
        
        result_df = df.copy()
        
        for col in columns:
            if col not in df.columns:
                continue
                
            if method == "onehot":
                dummies = pd.get_dummies(df[col], prefix=col)
                result_df = pd.concat([result_df.drop(col, axis=1), dummies], axis=1)
            elif method == "label":
                from sklearn.preprocessing import LabelEncoder
                le = LabelEncoder()
                result_df[col] = le.fit_transform(df[col].astype(str))
        
        return result_df
    
    def remove_outliers(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        """去除异常值"""
        method = params.get("method", "iqr")
        columns = params.get("columns", df.select_dtypes(include=[np.number]).columns.tolist())
        factor = params.get("factor", 1.5)
        
        result_df = df.copy()
        
        for col in columns:
            if col not in df.columns or df[col].dtype not in ['int64', 'float64']:
                continue
                
            if method == "iqr":
                Q1 = df[col].quantile(0.25)
                Q3 = df[col].quantile(0.75)
                IQR = Q3 - Q1
                lower_bound = Q1 - factor * IQR
                upper_bound = Q3 + factor * IQR
                result_df = result_df[(result_df[col] >= lower_bound) & (result_df[col] <= upper_bound)]
            elif method == "zscore":
                z_scores = np.abs((df[col] - df[col].mean()) / df[col].std())
                result_df = result_df[z_scores < factor]
        
        return result_df
    
    def select_columns(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        """选择列"""
        columns = params.get("columns", df.columns.tolist())
        return df[columns]
    
    def filter_rows(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        """过滤行"""
        conditions = params.get("conditions", [])
        
        result_df = df.copy()
        
        for condition in conditions:
            column = condition.get("column")
            operator = condition.get("operator", "==")
            value = condition.get("value")
            
            if column not in df.columns:
                continue
                
            if operator == "==":
                result_df = result_df[result_df[column] == value]
            elif operator == "!=":
                result_df = result_df[result_df[column] != value]
            elif operator == ">":
                result_df = result_df[result_df[column] > value]
            elif operator == ">=":
                result_df = result_df[result_df[column] >= value]
            elif operator == "<":
                result_df = result_df[result_df[column] < value]
            elif operator == "<=":
                result_df = result_df[result_df[column] <= value]
            elif operator == "contains":
                result_df = result_df[result_df[column].astype(str).str.contains(str(value), na=False)]
            elif operator == "in":
                result_df = result_df[result_df[column].isin(value)]
        
        return result_df
    
    def transform_column(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        """转换列"""
        column = params.get("column")
        operation = params.get("operation")
        params_dict = params.get("params", {})
        
        if column not in df.columns:
            raise HTTPException(400, f"列 '{column}' 不存在")
        
        result_df = df.copy()
        
        if operation == "log":
            result_df[column] = np.log1p(df[column])
        elif operation == "sqrt":
            result_df[column] = np.sqrt(df[column])
        elif operation == "square":
            result_df[column] = df[column] ** 2
        elif operation == "abs":
            result_df[column] = np.abs(df[column])
        elif operation == "round":
            decimals = params_dict.get("decimals", 0)
            result_df[column] = df[column].round(decimals)
        elif operation == "datetime":
            if df[column].dtype == 'object':
                result_df[column] = pd.to_datetime(df[column])
        elif operation == "extract":
            pattern = params_dict.get("pattern")
            if pattern:
                result_df[column] = df[column].astype(str).str.extract(pattern)
        
        return result_df
    
    def get_data_profile(self, df: pd.DataFrame) -> Dict[str, Any]:
        """获取数据概览"""
        # 转换numpy类型为Python原生类型，以便JSON序列化
        def convert_types(obj):
            if isinstance(obj, dict):
                return {k: convert_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_types(v) for v in obj]
            elif isinstance(obj, (np.integer, np.int64, np.int32)):
                return int(obj)
            elif isinstance(obj, (np.floating, np.float64, np.float32)):
                return float(obj)
            elif isinstance(obj, np.bool_):
                return bool(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            else:
                return obj
        
        profile = {
            "shape": df.shape,
            "columns": df.columns.tolist(),
            "dtypes": {str(col): str(dtype) for col, dtype in df.dtypes.items()},
            "memory_usage": int(df.memory_usage(deep=True).sum()),
            "missing_values": {str(col): int(count) for col, count in df.isnull().sum().items()},
            "duplicates": int(df.duplicated().sum()),
        }
        
        # 数值列统计
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        if len(numeric_cols) > 0:
            describe_stats = df[numeric_cols].describe()
            profile["numeric_stats"] = convert_types(describe_stats.to_dict())
        
        # 分类列统计
        categorical_cols = df.select_dtypes(include=['object']).columns
        if len(categorical_cols) > 0:
            profile["categorical_stats"] = {}
            for col in categorical_cols:
                profile["categorical_stats"][str(col)] = {
                    "unique_count": int(df[col].nunique()),
                    "top_values": {str(k): int(v) for k, v in df[col].value_counts().head(5).items()}
                }
        
        return profile