"""回归任务实现"""
from __future__ import annotations

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, TensorDataset

from app.training.core import BaseTask, register_task, TrainingSpec, TrainingContext
from app.trainer import _prepare_features, _build_model, _build_optimizer, _build_scheduler, _save_checkpoint
from app.predictor import PICKLE_FORMAT
from app.trainer import _estimator_framework
from app.device import select_device


@register_task
class RegressionTask(BaseTask):
    task_name = "regression"
    supported_models = ["mlp", "linear_regression", "decision_tree", "random_forest",
                        "gradient_boosting", "svm", "knn", "xgboost"]
    
    def prepare_data(self) -> tuple:
        spec = self.spec
        context = self.context
        log = self.log
        
        dataset_path = context.dataset_path
        target = spec.target
        params = spec.params
        
        test_split = float(params.get("test_split", 0.2))
        seed = int(params.get("seed", 42))
        model_type = str(params.get("model_type", "mlp"))
        
        df = pd.read_csv(dataset_path)
        if target not in df.columns:
            raise ValueError(f"目标列 '{target}' 不在数据集中")
        
        df = df.dropna(subset=[target]).reset_index(drop=True)
        if len(df) < 10:
            raise ValueError(f"有效样本过少（{len(df)} 行，需要 >= 10 行）")
        
        X_df, feat_meta = _prepare_features(df, target)
        y_all = pd.to_numeric(df[target], errors="raise").to_numpy(dtype=np.float32)
        X_all = X_df.to_numpy(dtype=np.float32)
        feature_names = [str(c) for c in X_df.columns]
        
        log(f"样本 {len(X_all)} 条；特征 {len(feature_names)} 个")
        
        X_train, X_val, y_train, y_val = train_test_split(
            X_all, y_all, test_size=test_split, random_state=seed
        )
        log(f"划分: 训练 {X_train.shape[0]} 条 / 验证 {X_val.shape[0]} 条")
        
        mean = np.asarray(X_train.mean(axis=0), dtype=np.float32)
        std = np.asarray(X_train.std(axis=0), dtype=np.float32)
        std[std < 1e-8] = 1.0
        np.subtract(X_train, mean, out=X_train)
        np.divide(X_train, std, out=X_train)
        np.subtract(X_val, mean, out=X_val)
        np.divide(X_val, std, out=X_val)
        
        # 目标标准化
        y_mean = float(np.asarray(y_train, dtype=np.float64).mean())
        y_std = float(np.asarray(y_train, dtype=np.float64).std())
        if y_std < 1e-8:
            y_std = 1.0
        y_scale = (y_mean, y_std)
        np.subtract(y_train, y_mean, out=y_train)
        np.divide(y_train, y_std, out=y_train)
        np.subtract(y_val, y_mean, out=y_val)
        np.divide(y_val, y_std, out=y_val)
        
        batch_size = int(params.get("batch_size", 256))
        train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
        val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=False)
        val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)
        
        preprocessor = {
            "feature_names": feature_names,
            "num_cols": feat_meta["num_cols"],
            "cat_cols": feat_meta["cat_cols"],
            "num_medians": feat_meta["num_medians"],
            "cat_modes": feat_meta["cat_modes"],
            "mean": mean.tolist(),
            "std": std.tolist(),
            "classes": None,
            "target": target,
            "task": "regression",
            "target_scale": y_scale,
        }
        
        metadata = {
            "rows": {"total": len(df), "train": len(X_train), "val": len(X_val)},
            "features": len(feature_names),
            "classes": None,
        }
        
        return train_loader, val_loader, preprocessor, metadata
    
    def build_model(self, preprocessor: Dict, metadata: Dict) -> torch.nn.Module:
        spec = self.spec
        params = spec.params
        
        in_dim = len(preprocessor["feature_names"])
        out_dim = 1
        model_type = str(params.get("model_type", "mlp"))
        
        if model_type == "mlp":
            hidden_size = int(params.get("hidden_size", 128))
            hidden_layers = int(params.get("hidden_layers", 2))
            dropout = float(params.get("dropout", 0.15))
            activation = str(params.get("activation", "relu"))
            device = self.context.device
            return _build_model(in_dim, out_dim, hidden_size, hidden_layers, device, dropout, activation)
        
        return None
    
    def build_optimizer(self, model: torch.nn.Module) -> torch.optim.Optimizer:
        spec = self.spec
        params = spec.params
        model_type = str(params.get("model_type", "mlp"))
        
        if model_type != "mlp":
            return None
        
        lr = float(params.get("lr", 1e-3))
        weight_decay = float(params.get("weight_decay", 0.01))
        optimizer_name = str(params.get("optimizer", "adamw"))
        return _build_optimizer(optimizer_name, model, lr, weight_decay)
    
    def build_scheduler(self, optimizer: torch.optim.Optimizer):
        spec = self.spec
        params = spec.params
        model_type = str(params.get("model_type", "mlp"))
        
        if model_type != "mlp" or optimizer is None:
            return None
        
        epochs = int(params.get("epochs", 20))
        scheduler_name = str(params.get("scheduler", "none"))
        return _build_scheduler(scheduler_name, optimizer, epochs)
    
    def train_step(self, model: torch.nn.Module, batch, optimizer) -> Dict[str, float]:
        spec = self.spec
        params = spec.params
        model_type = str(params.get("model_type", "mlp"))
        
        if model_type == "mlp":
            return self._train_step_mlp(model, batch, optimizer)
        return {"train_loss": 0.0}
    
    def _train_step_mlp(self, model: torch.nn.Module, batch, optimizer) -> Dict[str, float]:
        X_batch, y_batch = batch
        device = self.context.device
        X_batch = X_batch.to(device)
        y_batch = y_batch.to(device)
        
        optimizer.zero_grad()
        out = model(X_batch).squeeze(-1)
        loss = nn.MSELoss()(out, y_batch)
        loss.backward()
        optimizer.step()
        
        return {"train_loss": loss.item()}
    
    def validate(self, model: torch.nn.Module, val_loader: DataLoader) -> Dict[str, float]:
        spec = self.spec
        params = spec.params
        model_type = str(params.get("model_type", "mlp"))
        
        if model_type == "mlp":
            return self._validate_mlp(model, val_loader)
        return {"val_loss": 0.0, "val_mse": 0.0, "val_rmse": 0.0, "val_mae": 0.0, "val_r2": 0.0}
    
    def _validate_mlp(self, model: torch.nn.Module, val_loader: DataLoader) -> Dict[str, float]:
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
                out = model(X_batch).squeeze(-1)
                loss = loss_fn(out, y_batch)
                total_loss += loss.item() * len(y_batch)
                preds.append(out.cpu().numpy())
                trues.append(y_batch.cpu().numpy())
        
        y_pred = np.concatenate(preds)
        y_true = np.concatenate(trues)
        n = len(y_true)
        mse = total_loss / max(n, 1)
        
        # 反归一化
        preprocessor = self.spec.params.get("_preprocessor", {})
        y_scale = preprocessor.get("target_scale", (0, 1))
        k = y_scale[1] ** 2
        k_mae = y_scale[1]
        
        val_mse = float(mean_squared_error(y_true, y_pred)) * k
        val_rmse = float(np.sqrt(val_mse))
        val_mae = float(mean_absolute_error(y_true, y_pred)) * k_mae
        val_r2 = float(r2_score(y_true, y_pred))
        
        return {
            "val_loss": mse * k,
            "val_mse": val_mse,
            "val_rmse": val_rmse,
            "val_mae": val_mae,
            "val_r2": val_r2,
        }
    
    def save_model(self, model: torch.nn.Module, preprocessor: Dict, path: Path, is_best: bool = False):
        spec = self.spec
        params = spec.params
        model_type = str(params.get("model_type", "mlp"))
        
        if model_type == "mlp":
            _save_checkpoint(path, model, {"in_dim": len(preprocessor["feature_names"]), 
                                            "out_dim": 1, 
                                            "model_type": "mlp"},
                           preprocessor, "regression", kind="best" if is_best else "last", record={})
        else:
            import pickle
            payload = {
                "format": PICKLE_FORMAT,
                "framework": _estimator_framework(model_type),
                "model_type": model_type,
                "estimator": model,
                "task": "regression",
                "kind": "best" if is_best else "last",
                "best_record": {},
                "preprocessor": preprocessor,
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    
    def load_model(self, path: Path) -> tuple:
        import torch
        checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        return checkpoint.get("estimator", checkpoint.get("state_dict")), checkpoint.get("preprocessor", {}), {}