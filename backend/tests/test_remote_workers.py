"""远程 worker 安全、SSH 命令构造、异步操作状态和单文件脚本测试。"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

import app.config as config
import app.remote_workers as remote_workers
from app.state import manager


def _remote_config(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """配置隔离的 SSH 密钥与主机指纹文件，不读取真实部署凭证。"""
    key = tmp_path / "worker_key"
    key.write_text("test-key", encoding="utf-8")
    key.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("compute.example.net ssh-ed25519 AAAATEST\n", encoding="utf-8")
    monkeypatch.setattr(config, "WORKER_REMOTE_ENABLED", True)
    monkeypatch.setattr(config, "WORKER_REMOTE_TOKEN", "test-remote-admin-token-0123456789")
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "registry.example.net/ml/worker:unit")
    monkeypatch.setattr(config, "WORKER_REMOTE_REDIS_URL", "rediss://redis.example.net:6380/0")
    monkeypatch.setattr(config, "WORKER_REMOTE_S3_ENDPOINT", "storage.example.net:9000")
    monkeypatch.setattr(config, "REDIS_URL", "redis://redis.example.net:6379/0")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(config, "S3_ACCESS_KEY", "access-test")
    monkeypatch.setattr(config, "S3_SECRET_KEY", "secret-test")
    monkeypatch.setattr(config, "S3_BUCKET", "models-test")
    monkeypatch.setattr(config, "S3_SECURE", True)
    monkeypatch.setattr(config, "WORKER_SSH_KEY_PATH", str(key))
    monkeypatch.setattr(config, "WORKER_SSH_KNOWN_HOSTS_PATH", str(known_hosts))
    return key, known_hosts


def _server(**overrides) -> dict:
    """构造无敏感凭证的远程服务器记录。"""
    value = {
        "machine_id": "remote-test-01",
        "host": "compute.example.net",
        "username": "worker",
        "port": 2222,
        "mode": "cpu",
        "cpu_slots": 2,
        "status": "provisioning",
    }
    value.update(overrides)
    return value


def test_remote_configuration_requires_key_permissions_and_registry(tmp_path, monkeypatch):
    """远程执行未启用、密钥权限过宽或镜像未指向仓库时均应被拒绝。"""
    key, _ = _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    assert remote_workers.remote_configuration_errors() == []

    key.chmod(0o644)
    assert any("权限必须限制为 600" in item for item in remote_workers.remote_configuration_errors())
    key.chmod(0o600)
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "worker:latest")
    assert any("镜像仓库引用" in item for item in remote_workers.remote_configuration_errors())
    monkeypatch.setattr(config, "WORKER_REMOTE_ENABLED", False)
    assert any("尚未启用" in item for item in remote_workers.remote_configuration_errors())


def test_remote_configuration_requires_shared_queue_and_object_storage(tmp_path, monkeypatch):
    """平台未启用共享 Redis 或 S3 时不允许部署无法协同的远程 worker。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(config, "REDIS_URL", "")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    errors = remote_workers.remote_configuration_errors()
    assert any("共享 REDIS_URL" in item for item in errors)
    assert any("共享 S3 存储" in item for item in errors)


def test_render_remote_script_quotes_values_and_does_not_transfer_repository(tmp_path, monkeypatch):
    """特殊字符只作为 shell 字符串传递，脚本只运行容器而不克隆项目源码。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "S3_SECRET_KEY", "secret$(touch /tmp/not-run) ' quoted")
    rendered = remote_workers.render_remote_script(_server(), "install")

    assert "export MTP_S3_SECRET_KEY='secret$(touch /tmp/not-run) '\"'\"' quoted'" in rendered
    assert "docker pull \"$MTP_WORKER_IMAGE\"" in rendered
    assert "git clone" not in rendered
    assert "docker-compose" not in rendered


def test_run_remote_action_pins_host_key_and_keeps_secrets_out_of_argv(tmp_path, monkeypatch):
    """SSH 使用严格主机指纹校验，令牌和存储密钥只在标准输入脚本中出现。"""
    key, known_hosts = _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    calls = {}

    def fake_run(command, **kwargs):
        calls["command"] = command
        calls["input"] = kwargs["input"]
        calls["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(command, 0, stdout="worker running\n", stderr="")

    monkeypatch.setattr(remote_workers.subprocess, "run", fake_run)
    result = remote_workers.run_remote_action(_server(), "install")

    assert result["stdout"].strip() == "worker running"
    command = calls["command"]
    assert command[command.index("-i") + 1] == str(key)
    assert command[command.index("-p") + 1] == "2222"
    assert "StrictHostKeyChecking=yes" in command
    assert f"UserKnownHostsFile={known_hosts}" in command
    assert command[-2:] == ["worker@compute.example.net", "bash -s"]
    assert calls["timeout"] == 3600
    assert "secret-test" not in " ".join(command)
    assert "secret-test" in calls["input"]
    assert config.WORKER_REMOTE_TOKEN not in calls["input"]


def test_run_remote_action_brackets_ipv6_ssh_destination(tmp_path, monkeypatch):
    """IPv6 目标主机以 OpenSSH 支持的方括号形式构造连接目标。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    calls = {}

    def fake_run(command, **kwargs):
        calls["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(remote_workers.subprocess, "run", fake_run)
    remote_workers.run_remote_action(_server(host="2001:db8::12"), "start")
    assert calls["command"][-2] == "worker@[2001:db8::12]"


@pytest.mark.parametrize("invalid_server", [
    _server(host="compute.example.net;id"),
    _server(host="--proxy-command=touch"),
    _server(username="user;id"),
    _server(machine_id="../escape"),
    _server(port=65536),
])
def test_render_remote_script_rejects_shell_and_path_injection(invalid_server):
    """主机、用户、机器标识和端口恶意输入在脚本构造前拒绝。"""
    with pytest.raises(remote_workers.RemoteWorkerError):
        remote_workers.validate_server_record(invalid_server)


def test_execute_remote_operation_persists_success_and_failure(monkeypatch):
    """远程执行结果更新 Redis/嵌入式状态，异常不写入密钥或堆栈信息。"""
    machine_id = "remote-operation-test"
    operation_id = "operation-test"
    server = _server(machine_id=machine_id)
    manager.lease.set_remote_server(machine_id, server)
    manager.lease.set_remote_operation(
        operation_id,
        {"operation_id": operation_id, "machine_id": machine_id, "action": "start", "status": "queued"},
    )
    monkeypatch.setattr(remote_workers, "run_remote_action", lambda *_args: {"stdout": "container started"})

    remote_workers.execute_remote_operation(operation_id, server, "start")
    result = manager.lease.get_remote_operation(operation_id)
    assert result["status"] == "completed"
    assert result["output"] == "container started"
    assert manager.lease.get_remote_server(machine_id)["status"] == "online"

    def fail_remote(*_args):
        raise remote_workers.RemoteWorkerError("SSH 登录失败")

    manager.lease.set_remote_operation(operation_id, {"operation_id": operation_id, "status": "queued"})
    monkeypatch.setattr(remote_workers, "run_remote_action", fail_remote)
    remote_workers.execute_remote_operation(operation_id, server, "stop")
    result = manager.lease.get_remote_operation(operation_id)
    assert result["status"] == "failed"
    assert result["error"] == "SSH 登录失败"
    assert manager.lease.get_remote_server(machine_id)["status"] == "error"


def test_remote_bootstrap_script_installs_cpu_and_gpu_workers(tmp_path: Path):
    """远程单文件脚本仅拉取镜像、写私有配置并按 CPU/GPU 模式启动容器。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "if [[ \"$1\" == inspect && \"${DOCKER_MISSING:-false}\" == true ]]; then exit 1; fi\n"
        "exit \"${DOCKER_STUB_EXIT:-0}\"\n",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)
    script = Path(remote_workers.__file__).with_name("remote_worker.sh")

    for mode, expected_gpu in (("cpu", False), ("gpu", True)):
        home = tmp_path / mode
        home.mkdir()
        env = os.environ.copy()
        env.update({
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "HOME": str(home),
            "DOCKER_LOG": str(docker_log),
            "MTP_ACTION": "install",
            "MTP_MACHINE_ID": f"remote-{mode}",
            "MTP_MODE": mode,
            "MTP_CPU_SLOTS": "2",
            "MTP_WORKER_IMAGE": "registry.example.net/ml/worker:unit",
            "MTP_REDIS_URL": "rediss://redis.example.net:6380/0",
            "MTP_S3_ENDPOINT": "storage.example.net:9000",
            "MTP_S3_ACCESS_KEY": "access-test",
            "MTP_S3_SECRET_KEY": "secret-test",
            "MTP_S3_BUCKET": "models-test",
            "MTP_S3_SECURE": "true",
        })
        result = subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)

        assert result.returncode == 0, result.stderr
        env_file = home / ".config" / "mtp-worker" / f"remote-{mode}.env"
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
        saved = env_file.read_text(encoding="utf-8")
        assert "WORKER_MANAGED=true" in saved
        assert "S3_SECRET_KEY=secret-test" in saved
        commands = docker_log.read_text(encoding="utf-8")
        assert "pull registry.example.net/ml/worker:unit" in commands
        assert "git clone" not in commands
        assert ("--gpus all" in commands) is expected_gpu
        assert "mtp-worker-remote-" in commands


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("start", "start mtp-worker-remote-control"),
        ("stop", "stop --time 45 mtp-worker-remote-control"),
        ("restart", "restart --time 45 mtp-worker-remote-control"),
        ("status", "inspect --format {{.State.Status}} mtp-worker-remote-control"),
    ],
)
def test_remote_bootstrap_script_lifecycle(tmp_path: Path, action: str, expected: str):
    """目标机生命周期命令只操作指定 worker 容器，并保留优雅停止时间。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\nexit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        "HOME": str(tmp_path),
        "DOCKER_LOG": str(docker_log),
        "MTP_ACTION": action,
        "MTP_MACHINE_ID": "remote-control",
        "MTP_MODE": "cpu",
    })
    script = Path(remote_workers.__file__).with_name("remote_worker.sh")
    result = subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)

    assert result.returncode == 0, result.stderr
    commands = docker_log.read_text(encoding="utf-8").splitlines()
    assert expected in commands
