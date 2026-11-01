"""时序预测任务实现"""
from __future__ import annotations

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from torch.utils.data import DataLoader, TensorDataset

from app.training.core import BaseTask, register_task, TrainingSpec, TrainingContext
from app.trainer import _prepare_time_series_windows, _build_model, _build_optimizer, _build_scheduler, _save_checkpoint


@register_task
class TimeSeriesTask(BaseTask):
    task_name = "time_series_forecasting"
    supported_models = ["mlp", "lstm", "gru"]
    
    def prepare_data(self) -> tuple:
        spec = self.spec
        context = self.context
        log = self.log
        
        dataset_path = context.dataset_path
        target = spec.target
        params = spec.params
        
        time_column = str(params.get("time_column", "timestamp"))
        lookback = int(params.get("lookback", 24))
        horizon = int(params.get("forecast_horizon", 1))
        test_split = float(params.get("test_split", 0.2))
        seed = int(params.get("seed", 42))
        
        if target not in ["", "value"]:  # 时序任务目标列固定为 value
            pass
        
        windows, labels, ts_meta = _prepare_time_series_windows(
            pd.read_csv(dataset_path), target or "value", time_column, lookback, horizon
        )
        
        log(f"时序窗口: 样本 {len(windows)} 条，回看 {lookback}，预测 {horizon} 步")
        
        # 时间序列分割：按时间顺序，不打乱
        n = len(windows)
        split_idx = int(n * (1 - test_split))
        X_train, X_val = windows[:split_idx], windows[split_idx:]
        y_train, y_val = labels[:split_idx], labels[split_idx:]
        
        log(f"划分: 训练 {len(X_train)} 条 / 验证 {len(X_val)} 条")
        
        batch_size = int(params.get("batch_size", 128))
        train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
        val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=False, drop_last=False)
        val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)
        
        preprocessor = {
            "feature_names": [f"lag_{i}" for i in range(lookback)],
            "num_cols": [f"lag_{i}" for i in range(lookback)],
            "cat_cols": [],
            "num_medians": {},
            "cat_modes": {},
            "mean": np.zeros(lookback, dtype=np.float32).tolist(),
            "std": np.ones(lookback, dtype=np.float32).tolist(),
            "classes": None,
            "target": target or "value",
            "task": "time_series_forecasting",
            "time_column": time_column,
            "lookback": lookback,
            "forecast_horizon": horizon,
        }
        
        metadata = {
            "rows": {"total": n, "train": len(X_train), "val": len(X_val)},
            "features": lookback,
            "classes": None,
            "time_column": time_column,
            "lookback": lookback,
            "forecast_horizon": horizon,
        }
        
        return train_loader, val_loader, preprocessor, metadata
    
    def build_model(self, preprocessor: Dict, metadata: Dict) -> torch.nn.Module:
        spec = self.spec
        params = spec.params
        
        in_dim = metadata["features"]  # lookback
        out_dim = 1  # 单步预测
        model_type = str(params.get("model_type", "mlp"))
        
        if model_type == "mlp":
            hidden_size = int(params.get("hidden_size", 128))
            hidden_layers = int(params.get("hidden_layers", 2))
            dropout = float(params.get("dropout", 0.15))
            activation = str(params.get("activation", "relu"))
            device = self.context.device
            return _build_model(in_dim, out_dim, hidden_size, hidden_layers, device, dropout, activation)
        
        elif model_type in ("lstm", "gru"):
            hidden_size = int(params.get("hidden_size", 128))
            num_layers = int(params.get("hidden_layers", 2))
            dropout = float(params.get("dropout", 0.15))
            device = self.context.device
            
            rnn_cls = nn.LSTM if model_type == "lstm" else nn.GRU
            return _TimeSeriesRNN(
                input_size=1,
                hidden_size=hidden_size,
                num_layers=num_layers,
                output_size=1,
                dropout=dropout,
                rnn_cls=rnn_cls,
            ).to(device)
        
        return None
    
    def build_optimizer(self, model: torch.nn.Module) -> torch.optim.Optimizer:
        spec = self.spec
        params = spec.params
        model_type = str(params.get("model_type", "mlp"))
        
        lr = float(params.get("lr", 1e-3))
        weight_decay = float(params.get("weight_decay", 0.01))
        optimizer_name = str(params.get("optimizer", "adamw"))
        return _build_optimizer(optimizer_name, model, lr, weight_decay)
    
    def build_scheduler(self, optimizer: torch.optim.Optimizer):
        spec = self.spec
        params = spec.params
        
        epochs = int(params.get("epochs", 20))
        scheduler_name = str(params.get("scheduler", "none"))
        return _build_scheduler(scheduler_name, optimizer, epochs)
    
    def train_step(self, model: torch.nn.Module, batch, optimizer) -> Dict[str, float]:
        X_batch, y_batch = batch
        device = self.context.device
        X_batch = X_batch.to(device)
        y_batch = y_batch.to(device)
        
        # RNN 需要 (batch, seq, feature) 格式
        if isinstance(model, _TimeSeriesRNN):
            X_batch = X_batch.unsqueeze(-1)  # (batch, seq, 1)
        
        optimizer.zero_grad()
        out = model(X_batch).squeeze(-1)
        loss = nn.MSELoss()(out, y_batch)
        loss.backward()
        optimizer.step()
        
        return {"train_loss": loss.item()}
    
    def validate(self, model: torch.nn.Module, val_loader: DataLoader) -> Dict[str, float]:
        device = self.context.device
        loss_fn = nn.MSELoss()
        model.eval()
        
        total_loss = 0.0
        preds = []
        trues = []
        
        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                X_batch = X_batch.to(device)
                y_batch = y_batch.to(device)
                
                if isinstance(model, _TimeSeriesRNN):
                    X_batch = X_batch.unsqueeze(-1)
                
                out = model(X_batch).squeeze(-1)
                loss = loss_fn(out, y_batch)
                total_loss += loss.item() * len(y_batch)
                preds.append(out.cpu().numpy())
                trues.append(y_batch.cpu().numpy())
        
        y_pred = np.concatenate(preds)
        y_true = np.concatenate(trues)
        n = len(y_true)
        mse = total_loss / max(n, 1)
        
        val_mse = float(mean_squared_error(y_true, y_pred))
        val_rmse = float(np.sqrt(val_mse))
        val_mae = float(mean_absolute_error(y_true, y_pred))
        val_r2 = float(r2_score(y_true, y_pred))
        
        return {
            "val_loss": mse,
            "val_mse": val_mse,
            "val_rmse": val_rmse,
            "val_mae": val_mae,
            "val_r2": val_r2,
        }
    
    def save_model(self, model: torch.nn.Module, preprocessor: Dict, path: Path, is_best: bool = False):
        _save_checkpoint(path, model, 
                       {"model_type": preprocessor.get("model_type", "mlp"), 
                        "in_dim": preprocessor.get("lookback", 24)},
                       preprocessor, "time_series_forecasting", kind="best" if is_best else "last", record={})
    
    def load_model(self, path: Path) -> tuple:
        import torch
        checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        return checkpoint.get("state_dict"), checkpoint.get("preprocessor", {}), {}


class _TimeSeriesRNN(nn.Module):
    """时序 RNN 模型"""
    
    def __init__(self, input_size: int, hidden_size: int, num_layers: int, 
                 output_size: int, dropout: float, rnn_cls):
        super().__init__()
        self.rnn = rnn_cls(input_size, hidden_size, num_layers, 
                          batch_first=True, dropout=dropout if num_layers > 1 else 0)
        self.fc = nn.Linear(hidden_size, output_size)
    
    def forward(self, x):
        out, _ = self.rnn(x)
        out = self.fc(out[:, -1, :])  # 取最后一个时间步
        return out