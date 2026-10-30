"""worker 节点管理脚本测试：隔离 Docker 命令并验证生命周期参数。"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "worker-node.sh"
VALID_ENV = (
    "REDIS_URL=redis://coordinator.example:6379/0\n"
    "S3_ENDPOINT=storage.example:9000\n"
    "WORKER_MACHINE_ID=worker-test\n"
)


def _invoke_script(
    tmp_path: Path,
    action: str,
    env_text: str | None = VALID_ENV,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """创建隔离的 Compose 配置与 docker 替身，再调用节点管理脚本。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "exit \"${DOCKER_STUB_EXIT:-0}\"\n",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)

    env_file = tmp_path / "worker.env"
    if env_text is not None:
        env_file.write_text(env_text, encoding="utf-8")
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text(
        "services:\n  worker:\n    image: example/worker\n",
        encoding="utf-8",
    )

    run_env = os.environ.copy()
    for key in (
        "REDIS_URL", "S3_ENDPOINT", "WORKER_MACHINE_ID", "S3_ACCESS_KEY",
        "S3_SECRET_KEY", "S3_BUCKET", "S3_SECURE", "WORKER_CPU_SLOTS",
        "WORKER_MANAGED",
    ):
        run_env.pop(key, None)
    run_env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{run_env.get('PATH', '')}",
            "DOCKER_LOG": str(docker_log),
            "WORKER_ENV_FILE": str(env_file),
            "WORKER_COMPOSE_FILE": str(compose_file),
        }
    )
    if extra_env:
        run_env.update(extra_env)
    result = subprocess.run(
        [str(SCRIPT), action],
        text=True,
        capture_output=True,
        env=run_env,
        check=False,
    )
    return result, docker_log


def test_add_builds_and_starts_only_worker(tmp_path: Path) -> None:
    """add 应构建 worker，且不启动目标服务器上的 Redis 或 S3。"""
    result, docker_log = _invoke_script(tmp_path, "add")

    assert result.returncode == 0, result.stderr
    commands = docker_log.read_text(encoding="utf-8").splitlines()
    assert any(line.endswith("up --detach --build --no-deps worker") for line in commands)
    assert all(" redis" not in line and " seaweedfs" not in line for line in commands)
    assert "等待主节点接收心跳" in result.stdout


def test_start_creates_worker_without_rebuilding(tmp_path: Path) -> None:
    """start 应仅启动 worker，便于恢复已经构建好的节点容器。"""
    result, docker_log = _invoke_script(tmp_path, "start")

    assert result.returncode == 0, result.stderr
    commands = docker_log.read_text(encoding="utf-8").splitlines()
    assert any(line.endswith("up --detach --no-deps worker") for line in commands)
    assert all("--build" not in line for line in commands)
    assert all(" redis" not in line and " seaweedfs" not in line for line in commands)


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("stop", "stop --timeout 45 worker"),
        ("status", "ps worker"),
    ],
)
def test_worker_lifecycle_commands(
    tmp_path: Path, action: str, expected: str
) -> None:
    """停止保留足够的 SIGTERM 等待时间，状态查询只看本机 worker。"""
    result, docker_log = _invoke_script(tmp_path, action)

    assert result.returncode == 0, result.stderr
    commands = docker_log.read_text(encoding="utf-8").splitlines()
    assert commands[-1].endswith(expected)


def test_restart_applies_worker_env_then_restarts(tmp_path: Path) -> None:
    """restart 先 up 应用 .env.worker 的最新配置（变化时 Compose 会重建容器），
    再强制重启进程，保证配置更新后重启也能生效。"""
    result, docker_log = _invoke_script(tmp_path, "restart")

    assert result.returncode == 0, result.stderr
    commands = docker_log.read_text(encoding="utf-8").splitlines()
    up_index = next(
        i for i, line in enumerate(commands)
        if line.endswith("up --detach --no-deps worker")
    )
    restart_index = next(
        i for i, line in enumerate(commands)
        if line.endswith("restart --timeout 45 worker")
    )
    assert up_index < restart_index
    assert "本机 worker 已重启" in result.stdout


@pytest.mark.parametrize(
    ("env_text", "missing_key"),
    [
        ("S3_ENDPOINT=storage.example:9000\n", "REDIS_URL"),
        ("REDIS_URL=redis://coordinator.example:6379/0\n", "S3_ENDPOINT"),
    ],
)
def test_add_requires_coordinator_and_storage_settings(
    tmp_path: Path, env_text: str, missing_key: str
) -> None:
    """add 缺少任一共享服务地址时应停止，不创建未接入集群的 worker。"""
    result, docker_log = _invoke_script(tmp_path, "add", env_text=env_text)

    assert result.returncode != 0
    assert f"缺少必需配置 {missing_key}" in result.stderr
    assert not docker_log.exists()


def test_add_requires_interactive_first_run_configuration(tmp_path: Path) -> None:
    """首次运行缺少连接信息且没有终端输入时，应安全失败而不启动空配置 worker。"""
    result, docker_log = _invoke_script(tmp_path, "add", env_text=None)

    assert result.returncode != 0
    assert "首次接入需要交互输入" in result.stderr
    assert not docker_log.exists()


def test_add_writes_private_worker_configuration(tmp_path: Path) -> None:
    """首次接入配置写入独立的私有 env 文件，并转义 Compose 插值字符。"""
    secret = 'secret$with"quotes'
    result, docker_log = _invoke_script(
        tmp_path,
        "add",
        env_text=None,
        extra_env={
            "REDIS_URL": "redis://coordinator.example:6379/0",
            "S3_ENDPOINT": "storage.example:9000",
            "WORKER_MACHINE_ID": "worker-safe",
            "S3_SECRET_KEY": secret,
        },
    )

    env_file = tmp_path / "worker.env"
    saved = env_file.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert 'WORKER_MACHINE_ID="worker-safe"' in saved
    assert 'WORKER_MANAGED=true' in saved
    assert 'S3_SECRET_KEY="secret$$with\\"quotes"' in saved
    assert secret not in result.stdout
    assert docker_log.exists()


def test_add_rejects_newlines_in_environment_values(tmp_path: Path) -> None:
    """配置值包含换行时应拒绝写文件，防止注入额外 Compose 环境项。"""
    result, docker_log = _invoke_script(
        tmp_path,
        "add",
        env_text=None,
        extra_env={
            "REDIS_URL": "redis://coordinator.example:6379/0",
            "S3_ENDPOINT": "storage.example:9000",
            "WORKER_MACHINE_ID": "worker-safe",
            "S3_SECRET_KEY": "secret\nWORKER_MANAGED=false",
        },
    )

    assert result.returncode == 2
    assert "不得包含换行符" in result.stderr
    assert not (tmp_path / "worker.env").exists()
    assert not docker_log.exists()


def test_unknown_action_is_rejected(tmp_path: Path) -> None:
    """非法生命周期操作应给出用法并且不调用 Docker。"""
    result, docker_log = _invoke_script(tmp_path, "delete")

    assert result.returncode == 2
    assert "未知操作" in result.stderr
    assert not docker_log.exists()


def test_add_propagates_compose_failure(tmp_path: Path) -> None:
    """Compose 校验失败时应向调用者返回错误码，不伪报添加成功。"""
    result, docker_log = _invoke_script(
        tmp_path, "add", extra_env={"DOCKER_STUB_EXIT": "17"}
    )

    assert result.returncode == 17
    commands = docker_log.read_text(encoding="utf-8").splitlines()
    assert commands[-1].endswith("config --quiet")


def test_environment_file_is_not_executed_as_shell(tmp_path: Path) -> None:
    """环境文件按 Compose 数据读取，不执行其中的 shell 表达式。"""
    marker = tmp_path / "should-not-exist"
    env_text = (
        f"REDIS_URL=$(touch {marker})\n"
        "S3_ENDPOINT=storage.example:9000\n"
        "WORKER_MACHINE_ID=worker-test\n"
    )
    result, docker_log = _invoke_script(tmp_path, "add", env_text=env_text)

    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert docker_log.exists()
