"""平台 Compose 启动脚本的模式选择与参数校验测试。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = PROJECT_ROOT / "scripts" / "start-platform.sh"


def _run_launcher(tmp_path: Path, overrides: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    """用临时 Docker 命令捕获启动脚本参数，避免测试实际启动服务。"""
    capture_path = tmp_path / "docker-args.txt"
    docker_shim = tmp_path / "docker"
    docker_shim.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\0' \"$@\" > \"$CAPTURE_PATH\"\n",
        encoding="utf-8",
    )
    docker_shim.chmod(0o755)

    environment = os.environ.copy()
    environment.pop("PLATFORM_MODE", None)
    environment.pop("REMOTE_MANAGEMENT", None)
    environment.update(overrides)
    environment["CAPTURE_PATH"] = str(capture_path)
    environment["PATH"] = f"{tmp_path}{os.pathsep}{environment.get('PATH', '')}"
    result = subprocess.run(
        ["bash", str(LAUNCHER)],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    captured_args = capture_path.read_bytes().decode().rstrip("\0").split("\0") if capture_path.exists() else []
    return result, captured_args, capture_path


@pytest.mark.parametrize(
    ("overrides", "base_file", "remote_file"),
    [
        ({}, "docker-compose.cpu.yml", False),
        ({"PLATFORM_MODE": "cpu", "REMOTE_MANAGEMENT": "false"}, "docker-compose.cpu.yml", False),
        ({"PLATFORM_MODE": "gpu", "REMOTE_MANAGEMENT": "false"}, "docker-compose.yml", False),
        ({"PLATFORM_MODE": "cpu", "REMOTE_MANAGEMENT": "true"}, "docker-compose.cpu.yml", True),
        ({"PLATFORM_MODE": "gpu", "REMOTE_MANAGEMENT": "true"}, "docker-compose.yml", True),
    ],
)
def test_launcher_selects_compose_files(
    tmp_path: Path,
    overrides: dict[str, str],
    base_file: str,
    remote_file: bool,
) -> None:
    """验证 CPU/GPU 选择与远程覆盖开关的组合。"""
    result, args, _ = _run_launcher(tmp_path, overrides)

    expected = [
        "compose",
        "--project-directory",
        str(PROJECT_ROOT),
        "--file",
        str(PROJECT_ROOT / base_file),
    ]
    if remote_file:
        expected.extend(["--file", str(PROJECT_ROOT / "docker-compose.remote.yml")])
    expected.extend(["up", "--build", "-d"])
    assert result.returncode == 0, result.stderr
    assert args == expected


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"PLATFORM_MODE": "auto"}, "PLATFORM_MODE 必须是 gpu 或 cpu"),
        ({"REMOTE_MANAGEMENT": "yes"}, "REMOTE_MANAGEMENT 必须是 true 或 false"),
    ],
)
def test_launcher_rejects_invalid_selection_without_running_docker(
    tmp_path: Path,
    overrides: dict[str, str],
    error: str,
) -> None:
    """非法部署选择应在调用 Docker 前明确失败。"""
    result, args, capture_path = _run_launcher(tmp_path, overrides)

    assert result.returncode == 2
    assert error in result.stderr
    assert args == []
    assert not capture_path.exists()
