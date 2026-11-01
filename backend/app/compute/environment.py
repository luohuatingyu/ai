"""执行环境隔离：conda/venv/容器环境管理"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class EnvironmentSpec:
    """环境规格"""
    name: str
    python_version: str = "3.12"
    packages: List[str] = None  # pip 包列表
    conda_packages: List[str] = None  # conda 包列表
    pip_index: str = ""  # pip 源
    conda_channels: List[str] = None
    env_vars: Dict[str, str] = None
    gpu_enabled: bool = False
    cuda_version: str = ""
    
    def __post_init__(self):
        if self.packages is None:
            self.packages = []
        if self.conda_packages is None:
            self.conda_packages = []
        if self.conda_channels is None:
            self.conda_channels = ["conda-forge", "pytorch", "nvidia"]
        if self.env_vars is None:
            self.env_vars = {}


class EnvironmentManager:
    """环境管理器：创建、缓存、复用隔离环境"""
    
    def __init__(self, base_dir: Path = None):
        self.base_dir = base_dir or Path(os.environ.get("ENV_BASE_DIR", "/opt/envs"))
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._env_cache: Dict[str, Path] = {}
    
    def get_or_create(self, spec: EnvironmentSpec) -> Path:
        """获取或创建环境，返回环境路径"""
        env_key = self._compute_key(spec)
        
        if env_key in self._env_cache:
            return self._env_cache[env_key]
        
        env_path = self.base_dir / env_key
        if env_path.exists():
            self._env_cache[env_key] = env_path
            return env_path
        
        # 创建新环境
        self._create_environment(env_path, spec)
        self._env_cache[env_key] = env_path
        return env_path
    
    def _compute_key(self, spec: EnvironmentSpec) -> str:
        """计算环境缓存键"""
        import hashlib
        content = f"{spec.python_version}|{sorted(spec.packages)}|{sorted(spec.conda_packages)}|{spec.cuda_version}"
        return f"env-{hashlib.sha256(content.encode()).hexdigest()[:16]}"
    
    def _create_environment(self, env_path: Path, spec: EnvironmentSpec):
        """创建虚拟环境"""
        # 优先使用 conda（如果可用且有 conda 包）
        if spec.conda_packages and self._has_conda():
            self._create_conda_env(env_path, spec)
        else:
            self._create_venv(env_path, spec)
    
    def _has_conda(self) -> bool:
        try:
            subprocess.run(["conda", "--version"], capture_output=True, check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
    
    def _create_conda_env(self, env_path: Path, spec: EnvironmentSpec):
        """创建 conda 环境"""
        cmd = [
            "conda", "create", "-y", "-p", str(env_path),
            f"python={spec.python_version}",
        ]
        cmd.extend(spec.conda_packages)
        cmd.extend(spec.packages)
        
        if spec.cuda_version:
            cmd.append(f"cudatoolkit={spec.cuda_version}")
        
        subprocess.run(cmd, check=True)
        
        # 配置 pip 源
        if spec.pip_index:
            pip_path = env_path / "bin" / "pip"
            subprocess.run([str(pip_path), "config", "set", "global.index-url", spec.pip_index], check=True)
    
    def _create_venv(self, env_path: Path, spec: EnvironmentSpec):
        """创建 venv 环境"""
        venv.create(env_path, with_pip=True)
        
        pip_path = env_path / "bin" / "pip"
        
        # 升级 pip
        subprocess.run([str(pip_path), "install", "-U", "pip"], check=True)
        
        # 配置 pip 源
        if spec.pip_index:
            subprocess.run([str(pip_path), "config", "set", "global.index-url", spec.pip_index], check=True)
        
        # 安装包
        if spec.packages:
            subprocess.run([str(pip_path), "install"] + spec.packages, check=True)
    
    def run_in_env(self, env_path: Path, cmd: List[str], env: Dict = None) -> subprocess.CompletedProcess:
        """在环境中运行命令"""
        full_env = os.environ.copy()
        
        # 激活环境
        bin_path = env_path / "bin"
        full_env["PATH"] = f"{bin_path}:{full_env['PATH']}"
        full_env["VIRTUAL_ENV"] = str(env_path)
        full_env.pop("PYTHONHOME", None)
        
        if env:
            full_env.update(env)
        
        return subprocess.run(cmd, env=full_env, capture_output=True, text=True)
    
    def get_python_path(self, env_path: Path) -> Path:
        """获取环境中的 Python 解释器路径"""
        return env_path / "bin" / "python"
    
    def install_package(self, env_path: Path, package: str):
        """在环境中安装包"""
        pip_path = env_path / "bin" / "pip"
        subprocess.run([str(pip_path), "install", package], check=True)
    
    def remove_env(self, env_key: str):
        """删除环境"""
        if env_key in self._env_cache:
            import shutil
            shutil.rmtree(self._env_cache[env_key], ignore_errors=True)
            del self._env_cache[env_key]


class ContainerEnvironment:
    """容器环境：使用 Docker/Podman 运行"""
    
    def __init__(self, image: str, runtime: str = "nvidia"):
        self.image = image
        self.runtime = runtime
    
    def run(
        self, 
        cmd: List[str], 
        volumes: Dict[str, str] = None,
        env: Dict[str, str] = None,
        gpus: str = "all",
        workdir: str = "/workspace",
    ) -> subprocess.CompletedProcess:
        """在容器中运行命令"""
        docker_cmd = ["docker", "run", "--rm"]
        
        if self.runtime:
            docker_cmd.extend(["--runtime", self.runtime])
        
        if gpus:
            docker_cmd.extend(["--gpus", gpus])
        
        if volumes:
            for host, container in volumes.items():
                docker_cmd.extend(["-v", f"{host}:{container}"])
        
        if env:
            for k, v in env.items():
                docker_cmd.extend(["-e", f"{k}={v}"])
        
        docker_cmd.extend(["-w", workdir, self.image] + cmd)
        
        return subprocess.run(docker_cmd, capture_output=True, text=True)
    
    def build_image(self, dockerfile: str, tag: str, context: str = "."):
        """构建镜像"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".dockerfile", delete=False) as f:
            f.write(dockerfile)
            dockerfile_path = f.name
        
        try:
            subprocess.run(["docker", "build", "-t", tag, "-f", dockerfile_path, context], check=True)
        finally:
            os.unlink(dockerfile_path)


# 预定义环境模板
DEFAULT_ML_ENV = EnvironmentSpec(
    name="ml-base",
    python_version="3.12",
    packages=[
        "torch",
        "torchvision",
        "torchaudio",
        "numpy",
        "pandas",
        "scikit-learn",
        "xgboost",
        "matplotlib",
        "seaborn",
        "jupyter",
        "ipykernel",
    ],
    conda_packages=[
        "python=3.12",
        "pytorch",
        "torchvision",
        "torchaudio",
        "pytorch-cuda=12.1",
        "-c", "pytorch",
        "-c", "nvidia",
    ],
    gpu_enabled=True,
    cuda_version="12.1",
)


DEFAULT_CPU_ENV = EnvironmentSpec(
    name="cpu-base",
    python_version="3.12",
    packages=[
        "numpy",
        "pandas",
        "scikit-learn",
        "xgboost",
        "matplotlib",
        "seaborn",
        "jupyter",
        "ipykernel",
    ],
    gpu_enabled=False,
)


def get_default_env(gpu: bool = True) -> EnvironmentSpec:
    return DEFAULT_ML_ENV if gpu else DEFAULT_CPU_ENV