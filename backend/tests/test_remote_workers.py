"""远程 worker 安全、SSH 命令构造、异步操作状态和单文件脚本测试。"""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import app.config as config
import app.remote_workers as remote_workers
from app.state import manager


@pytest.fixture(autouse=True)
def _clear_page_credentials_after_test():
    """每个用例结束后清除测试写入的 SSH 凭证密文。"""
    yield
    manager.lease.delete_remote_ssh_credentials()
    if config.USE_DATABASE:
        from app.remote_config_repository import delete_ssh_credentials

        delete_ssh_credentials()


@pytest.fixture(autouse=True)
def _isolate_remote_mysql_test_store(remote_mysql_db):
    """为远程 worker 用例提供独立关系数据库会话。"""
    yield


def _remote_config(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """配置远程执行环境并创建隔离的页面凭证，不读取真实凭证。"""
    from cryptography.fernet import Fernet
    key = tmp_path / "worker_key"
    key.write_text("test-key", encoding="utf-8")
    key.chmod(0o600)
    known_hosts = tmp_path / "remote_known_hosts"
    monkeypatch.setattr(config, "USE_DATABASE", True)
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "registry.example.net/ml/worker:unit")
    monkeypatch.setattr(config, "WORKER_REMOTE_REDIS_URL", "rediss://redis.example.net:6380/0")
    monkeypatch.setattr(config, "WORKER_REMOTE_S3_ENDPOINT", "storage.example.net:9000")
    monkeypatch.setattr(config, "REDIS_URL", "redis://redis.example.net:6379/0")
    monkeypatch.setattr(config, "REDIS_PASSWORD", "redis-password-test")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(config, "S3_ACCESS_KEY", "access-test")
    monkeypatch.setattr(config, "S3_SECRET_KEY", "secret-test")
    monkeypatch.setattr(config, "S3_BUCKET", "models-test")
    monkeypatch.setattr(config, "S3_SECURE", True)
    monkeypatch.setattr(config, "WORKER_SSH_KNOWN_HOSTS_PATH", str(known_hosts))
    monkeypatch.setattr(config, "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(manager, "distributed", True)
    manager.lease.delete_remote_ssh_credentials()
    remote_workers.create_platform_ssh_credential({
        "name": "test default",
        "username": "worker",
        "auth_method": "key",
        "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----\n",
        "password": "",
        "passphrase": "",
    })
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


def _test_worker_app_archive_base64() -> str:
    """生成供本地 shell 用例使用的最小合法 worker 源码包。"""
    import base64
    import io
    import tarfile

    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz", format=tarfile.GNU_FORMAT) as archive:
        source = b"def worker_test_entrypoint():\n    return True\n"
        entry = tarfile.TarInfo("app/worker.py")
        entry.size = len(source)
        entry.mode = 0o644
        archive.addfile(entry, io.BytesIO(source))
    return base64.b64encode(archive_buffer.getvalue()).decode("ascii")


def test_remote_operation_log_is_redacted_and_bounded(tmp_path: Path, monkeypatch):
    """实时操作日志过滤 SSH/Redis/S3 凭证，并只保留最近的有界内容。"""
    _remote_config(tmp_path, monkeypatch)
    operation_id = "d" * 32
    private_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "verysecretpayloadline\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    credentials = {"password": "ssh-secret", "passphrase": "", "private_key": private_key}
    manager.lease.set_remote_operation(
        operation_id,
        {"operation_id": operation_id, "status": "running", "phase": "queued", "log": ""},
    )
    monkeypatch.setattr(remote_workers, "_REMOTE_OPERATION_LOG_LIMIT", 256)

    remote_workers._append_remote_operation_log(
        operation_id,
        "执行远程脚本",
        f"ssh-secret {config.REDIS_URL} {config.S3_SECRET_KEY} verysecretpayloadline",
        credentials=credentials,
        server=_server(),
    )
    redacted_log = manager.lease.get_remote_operation(operation_id)["log"]
    assert "[redacted]" in redacted_log
    assert "verysecretpayloadline" not in redacted_log
    for index in range(5):
        remote_workers._append_remote_operation_log(
            operation_id, "执行远程脚本", f"line-{index}-" + ("x" * 120), server=_server()
        )

    result = manager.lease.get_remote_operation(operation_id)
    assert len(result["log"]) <= 256
    assert result["log"].startswith("[较早日志已截断]")
    assert "ssh-secret" not in result["log"]
    assert config.REDIS_URL not in result["log"]
    assert config.S3_SECRET_KEY not in result["log"]
    assert "private-key" not in result["log"]
    assert "line-4" in result["log"]
    assert result["phase"] == "执行远程脚本"


def test_communicate_remote_process_streams_only_new_output_lines():
    """SSH 子进程超时轮询期间逐行发送新增 stdout/stderr，不重复整段输出。"""
    command = ["ssh", "worker@example.net", "bash -s"]

    class FakeProcess:
        args = command

        def __init__(self):
            self.calls = 0

        def communicate(self, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(
                    command,
                    timeout,
                    output="setup complete\nimage 50%",
                    stderr="warning: slow transfer\n",
                )
            return "setup complete\nimage 50% loaded\n", "warning: slow transfer\n"

    emitted = []
    process = FakeProcess()
    result = remote_workers._communicate_remote_process(
        process,
        operation_id=None,
        timeout=10,
        progress_callback=lambda phase, message: emitted.append((phase, message)),
        progress_phase="镜像加载",
    )

    assert result == ("setup complete\nimage 50% loaded\n", "warning: slow transfer\n")
    assert emitted == [
        ("镜像加载", "stdout: setup complete"),
        ("镜像加载", "stderr: warning: slow transfer"),
        ("镜像加载", "stdout: image 50% loaded"),
    ]

    class TimedOutProcess:
        args = command

        def __init__(self):
            self.killed = False

        def kill(self):
            self.killed = True

        def communicate(self):
            return "docker load stalled\n", "connection timed out\n"

    timed_out = TimedOutProcess()
    timeout_lines = []
    with pytest.raises(subprocess.TimeoutExpired):
        remote_workers._communicate_remote_process(
            timed_out,
            operation_id=None,
            timeout=0,
            progress_callback=lambda phase, message: timeout_lines.append(message),
        )
    assert timed_out.killed is True
    assert "stdout: docker load stalled" in timeout_lines
    assert "stderr: connection timed out" in timeout_lines


def test_communicate_remote_process_streams_carriage_return_progress():
    """Docker 以回车符刷新构建进度时也应实时发送日志，而不是等进程结束。"""
    command = ["ssh", "worker@example.net", "docker build -"]
    first_output = b" => [1/2] FROM python:3.12-slim\r => [2/2] RUN pip install\r"

    class FakeProcess:
        args = command

        def __init__(self):
            self.calls = 0

        def communicate(self, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(command, timeout, output=first_output, stderr=b"")
            return first_output + b"Build complete\n", b""

    emitted = []
    remote_workers._communicate_remote_process(
        FakeProcess(),
        operation_id=None,
        timeout=10,
        progress_callback=lambda phase, message: emitted.append((phase, message)),
        progress_phase="构建目标架构镜像",
    )

    assert emitted == [
        ("构建目标架构镜像", "stdout:  => [1/2] FROM python:3.12-slim"),
        ("构建目标架构镜像", "stdout:  => [2/2] RUN pip install"),
        ("构建目标架构镜像", "stdout: Build complete"),
    ]


def test_communicate_remote_process_reports_idle_target_build(monkeypatch):
    """目标机构建长时间无输出时发送等待提示，其他阶段不依赖此提示。"""
    command = ["ssh", "worker@example.net", "docker build -"]
    fake_clock = {"now": 0.0}
    monkeypatch.setattr(remote_workers, "_REMOTE_BUILD_IDLE_PROGRESS_INTERVAL", 30.0)
    monkeypatch.setattr(remote_workers.time, "monotonic", lambda: fake_clock["now"])

    class FakeProcess:
        args = command

        def __init__(self):
            self.calls = 0

        def communicate(self, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                fake_clock["now"] = 30.0
                raise subprocess.TimeoutExpired(command, timeout, output=b"", stderr=b"")
            return b"build complete\n", b""

    emitted = []
    remote_workers._communicate_remote_process(
        FakeProcess(),
        operation_id=None,
        timeout=None,
        progress_callback=lambda phase, message: emitted.append((phase, message)),
        progress_phase="构建目标架构镜像",
    )

    assert emitted == [
        (
            "构建目标架构镜像",
            "目标机仍在构建镜像，30 秒未收到新 Docker 输出；可能正在下载基础镜像或 Python 依赖。",
        ),
        ("构建目标架构镜像", "stdout: build complete"),
    ]


def test_run_remote_subprocess_preserves_binary_build_context(monkeypatch):
    """SSH 子进程以二进制模式接收 tar 上下文并返回可解码的构建输出。"""
    command = ["ssh", "worker@compute.example.net", "docker build -"]
    payload = b"docker-build-context-tar"
    received = {}

    class FakeProcess:
        args = command
        returncode = 0

        def communicate(self, input=None, timeout=None):
            received["input"] = input
            return b"built\n", b""

        def poll(self):
            return self.returncode

    def fake_popen(_command, **kwargs):
        received["text"] = kwargs["text"]
        return FakeProcess()

    monkeypatch.setattr(remote_workers.subprocess, "Popen", fake_popen)
    result = remote_workers._run_remote_subprocess(
        command,
        input_data=payload,
        timeout=10,
        environment=None,
        operation_id=None,
    )

    assert received == {"text": False, "input": payload}
    assert result.stdout == b"built\n"


def test_worker_heartbeat_probe_reports_present_and_missing_slots(monkeypatch):
    """启动探测能报告收到心跳及 15 秒内未收到心跳两种结果。"""
    operation_id = "e" * 32
    manager.lease.clear_remote_operation_cancel(operation_id)
    monkeypatch.setattr(remote_workers, "_WORKER_STARTUP_HEARTBEAT_TIMEOUT", 0.0)
    emitted = []
    monkeypatch.setattr(
        manager.lease,
        "list_workers",
        lambda: [{"worker_id": "remote-a-cpu0", "machine_id": "remote-a", "active": True}],
    )
    remote_workers._wait_for_remote_worker_heartbeat(
        operation_id, "remote-a", lambda phase, message: emitted.append((phase, message))
    )
    assert any("已收到 1 个资源槽心跳" in message for _phase, message in emitted)

    emitted.clear()
    monkeypatch.setattr(manager.lease, "list_workers", lambda: [])
    remote_workers._wait_for_remote_worker_heartbeat(
        operation_id, "remote-a", lambda phase, message: emitted.append((phase, message))
    )
    assert any("15 秒内未收到资源心跳" in message for _phase, message in emitted)


def test_run_remote_install_records_stages_and_worker_heartbeat(tmp_path, monkeypatch):
    """远程安装日志记录目标架构、目标机构建、worker 启动和首次资源心跳。"""
    _remote_config(tmp_path, monkeypatch)
    operation_id = "f" * 32
    server = _server()
    manager.lease.clear_remote_operation_cancel(operation_id)
    manager.lease.set_remote_operation(
        operation_id,
        {"operation_id": operation_id, "machine_id": server["machine_id"], "status": "running"},
    )
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(
        manager.lease,
        "list_workers",
        lambda: [{
            "worker_id": "remote-test-01-cpu0",
            "machine_id": "remote-test-01",
            "active": True,
        }],
    )

    def fake_run(command, **kwargs):
        if command[-1].startswith("docker info"):
            result = subprocess.CompletedProcess(command, 0, stdout="linux/x86_64\n", stderr="")
        else:
            result = subprocess.CompletedProcess(
                command, 0, stdout=f"worker started\n{config.REDIS_URL}\n", stderr=""
            )
        callback = kwargs.get("progress_callback")
        if callback:
            for line in result.stdout.splitlines():
                callback(kwargs.get("progress_phase", "远程命令"), f"stdout: {line}")
        return result

    def fake_build(*_args, progress_callback=None, **_kwargs):
        if progress_callback:
            progress_callback("构建目标架构镜像", "目标机使用缓存开始构建。")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    monkeypatch.setattr(remote_workers, "_build_worker_image_on_remote", fake_build)

    remote_workers.run_remote_action(server, "install", operation_id)

    result = manager.lease.get_remote_operation(operation_id)
    assert "检查镜像架构" in result["log"]
    assert "构建目标架构镜像" in result["log"]
    assert "传输并加载 worker 镜像" not in result["log"]
    assert "stdout: worker started" in result["log"]
    assert config.REDIS_URL not in result["log"]
    assert "[redacted]" in result["log"]
    assert "已收到 1 个资源槽心跳" in result["log"]


def _set_page_credential(auth_method: str, **overrides) -> dict:
    """替换测试用默认页面凭证，覆盖三类认证组合。"""
    manager.lease.delete_remote_ssh_credentials()
    from app.remote_config_repository import delete_ssh_credentials

    delete_ssh_credentials()
    credential = {
        "name": "test credential",
        "username": "worker",
        "auth_method": auth_method,
        "private_key": "",
        "password": "",
        "passphrase": "",
    }
    credential.update(overrides)
    return remote_workers.create_platform_ssh_credential(credential)


def test_remote_configuration_requires_page_credentials_and_platform_worker_image_tag(tmp_path, monkeypatch):
    """远程执行依赖页面凭证和平台 worker 镜像标签，不要求镜像仓库地址。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    assert remote_workers.remote_configuration_errors() == []

    missing_key = {"auth_method": "key", "private_key": "", "password": "", "passphrase": ""}
    assert any("页面未配置 SSH 私钥" in item for item in remote_workers.remote_configuration_errors(missing_key))
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "")
    assert any("平台 WORKER_IMAGE" in item for item in remote_workers.remote_configuration_errors())
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "worker:latest")
    assert not any("镜像" in item for item in remote_workers.remote_configuration_errors())


def test_remote_configuration_supports_page_password_without_key_file(tmp_path, monkeypatch):
    """页面密码认证不依赖私钥文件，主机密钥文件会在首次连接时自动建立。"""
    _, known_hosts = _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    credentials = {"auth_method": "password", "password": "page password", "passphrase": "", "private_key": ""}

    assert remote_workers.remote_configuration_errors(credentials) == []

    assert not known_hosts.exists()
    assert remote_workers.remote_configuration_errors(credentials) == []


def test_remote_configuration_requires_page_password_and_valid_auth_method(tmp_path, monkeypatch):
    """密码模式需要页面密码，未识别的认证模式应明确拒绝。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    missing_password = {"auth_method": "password", "password": "", "passphrase": "", "private_key": ""}
    assert any("登录密码" in item for item in remote_workers.remote_configuration_errors(missing_password))
    errors = remote_workers.remote_configuration_errors(
        {"auth_method": "unsupported", "password": "", "passphrase": "", "private_key": ""}
    )
    assert any("页面配置 SSH 凭证" in item for item in errors)


def test_remote_configuration_rejects_newline_in_page_password(tmp_path, monkeypatch):
    """页面密码不能包含换行，避免 askpass 凭证产生多行响应。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    newline_password = {"auth_method": "password", "password": "password\nsecond-line", "passphrase": "", "private_key": ""}
    assert any("SSH 凭证不能包含换行符" in item for item in remote_workers.remote_configuration_errors(newline_password))


@pytest.mark.parametrize(
    ("key_type", "key_options"),
    [
        ("ed25519", []),
        ("ecdsa", ["-b", "256"]),
        ("rsa", ["-b", "2048"]),
        ("rsa", ["-b", "2048", "-m", "PEM"]),
    ],
)
def test_openssh_reads_supported_private_key_formats(tmp_path, key_type, key_options):
    """OpenSSH 自行识别常见密钥格式，平台不强制转换或限定密钥算法。"""
    from app.routers.schemas import RemoteSSHCredentialsRequest

    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        pytest.skip("测试环境没有 ssh-keygen")
    key = tmp_path / "platform_key"
    subprocess.run(
        [ssh_keygen, "-q", "-t", key_type, *key_options, "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = subprocess.run(
        [ssh_keygen, "-y", "-f", str(key)], check=True, capture_output=True, text=True
    )

    assert parsed.stdout.startswith({
        "ed25519": "ssh-ed25519 ",
        "ecdsa": "ecdsa-sha2-nistp256 ",
        "rsa": "ssh-rsa ",
    }[key_type])
    configured = RemoteSSHCredentialsRequest(
        auth_method="key", private_key=key.read_text(encoding="utf-8")
    )
    assert configured.auth_method == "key"


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
    """特殊字符只作为 shell 字符串传递，脚本运行已加载镜像而不拉仓库或克隆源码。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "S3_SECRET_KEY", "secret$(touch /tmp/not-run) ' quoted")
    rendered = remote_workers.render_remote_script(_server(), "install")

    assert "export MTP_S3_SECRET_KEY='secret$(touch /tmp/not-run) '\"'\"' quoted'" in rendered
    assert "docker pull" not in rendered
    assert 'docker image inspect "$MTP_WORKER_IMAGE"' in rendered
    assert "git clone" not in rendered
    assert "docker-compose" not in rendered


def test_render_remote_script_includes_redis_password_and_effective_config_marks_auth(tmp_path, monkeypatch):
    """Redis 密码单独配置（URL 不含认证）时也必须下发，远程 worker 才能通过认证。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "REDIS_PASSWORD", "standalone-redis-pass")
    monkeypatch.setattr(config, "WORKER_REMOTE_REDIS_URL", "")
    monkeypatch.setattr(config, "REDIS_URL", "redis://redis.example.net:6379/0")

    rendered = remote_workers.render_remote_script(_server(), "config")
    assert "export MTP_REDIS_PASSWORD=standalone-redis-pass" in rendered

    settings = remote_workers.remote_deployment_settings({})
    assert settings["redis_password"] == "standalone-redis-pass"

    effective = remote_workers.remote_effective_config(_server())
    assert effective["redis_auth_configured"] is True
    assert "standalone-redis-pass" not in repr(effective)


def test_remote_deployment_settings_apply_platform_fallback_and_server_network_overrides(tmp_path, monkeypatch):
    """Worker 镜像只取平台配置，Redis/S3 未覆盖时回退平台 .env。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "registry.example.net/platform/worker:default")
    monkeypatch.setattr(config, "WORKER_REMOTE_REDIS_URL", "")
    monkeypatch.setattr(config, "WORKER_REMOTE_S3_ENDPOINT", "")
    monkeypatch.setattr(config, "REDIS_URL", "rediss://platform-redis.example.net:6380/0")
    monkeypatch.setattr(config, "S3_ENDPOINT", "platform-s3.example.net:9000")

    defaults = remote_workers.remote_deployment_settings({})
    assert defaults == {
        "worker_image": "registry.example.net/platform/worker:default",
        "redis_url": "rediss://platform-redis.example.net:6380/0",
        "redis_password": "redis-password-test",
        "s3_endpoint": "platform-s3.example.net:9000",
    }

    redis_url = "rediss://worker:private-pass@remote-redis.example.net:6380/4"
    server = remote_workers.apply_remote_server_overrides(
        _server(),
        {
            "remote_redis_url": redis_url,
            "remote_s3_endpoint": "remote-s3.example.net:9000",
        },
    )
    assert server["remote_redis_url_encrypted"] != redis_url
    assert "remote_redis_url" not in server
    assert remote_workers.remote_deployment_settings(server) == {
        "worker_image": "registry.example.net/platform/worker:default",
        "redis_url": redis_url,
        "redis_password": "redis-password-test",
        "s3_endpoint": "remote-s3.example.net:9000",
    }
    assert "worker_image_override" not in server
    fallback = remote_workers.apply_remote_server_overrides(
        server, {"remote_redis_url": "", "remote_s3_endpoint": ""}
    )
    assert remote_workers.remote_deployment_settings(fallback) == defaults
    public = remote_workers.remote_server_public_view(server)
    assert public["has_remote_redis_url_override"] is True
    assert "remote_redis_url_encrypted" not in public
    assert redis_url not in repr(public)
    assert remote_workers.remote_server_public_view({"status": "online"})["deployed"] is True
    assert remote_workers.remote_server_public_view({"status": "stopped"})["deployed"] is True
    assert remote_workers.remote_server_public_view(
        {"status": "online", "deployed": False}
    )["deployed"] is False
    assert remote_workers.remote_server_public_view(
        {"status": "running", "deployed": None}
    )["deployed"] is True


def test_remote_configuration_accepts_server_network_overrides_without_remote_env_values(tmp_path, monkeypatch):
    """远程 Redis/S3 可由单机覆盖值补齐，镜像仍由平台配置提供。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "registry.example.net/ml/worker:unit")
    monkeypatch.setattr(config, "WORKER_REMOTE_REDIS_URL", "")
    monkeypatch.setattr(config, "WORKER_REMOTE_S3_ENDPOINT", "")
    server = remote_workers.apply_remote_server_overrides(
        _server(),
        {
            "remote_redis_url": "rediss://redis.example.net:6380/0",
            "remote_s3_endpoint": "s3.example.net:9000",
        },
    )

    assert remote_workers.remote_configuration_errors(server=server) == []
    rendered = remote_workers.render_remote_script(server, "install")
    assert "registry.example.net/server/worker:v1" not in rendered
    assert "export MTP_WORKER_IMAGE=registry.example.net/ml/worker:unit" in rendered
    assert "export MTP_REDIS_URL=rediss://redis.example.net:6380/0" in rendered
    assert "export MTP_S3_ENDPOINT=s3.example.net:9000" in rendered


def test_remote_server_redis_override_can_be_preserved_replaced_or_cleared(tmp_path, monkeypatch):
    """编辑时空 Redis 输入清除密文并回退到平台 .env。"""
    _remote_config(tmp_path, monkeypatch)
    original = "redis://user:first-password@redis-one.example.net:6379/0"
    replacement = "rediss://user:second-password@redis-two.example.net:6380/2"
    server = remote_workers.apply_remote_server_overrides(
        _server(), {"remote_redis_url": original}
    )

    preserved = remote_workers.apply_remote_server_overrides(
        server, {"remote_redis_url": ""}
    )
    assert "remote_redis_url_encrypted" not in preserved
    assert remote_workers.remote_deployment_settings(preserved)["redis_url"] == config.WORKER_REMOTE_REDIS_URL
    replaced = remote_workers.apply_remote_server_overrides(
        preserved, {"remote_redis_url": replacement}
    )
    assert remote_workers.remote_deployment_settings(replaced)["redis_url"] == replacement
    cleared = remote_workers.apply_remote_server_overrides(
        replaced, {"clear_remote_redis_url_override": True}
    )
    assert "remote_redis_url_encrypted" not in cleared
    assert remote_workers.remote_server_public_view(cleared)["has_remote_redis_url_override"] is False


def test_remote_image_config_does_not_inherit_local_compose_fallback(monkeypatch):
    """本机 worker 的 Compose 默认镜像不应冒充远程平台默认镜像。"""
    monkeypatch.setenv("WORKER_IMAGE", "mtp-worker:local")
    monkeypatch.setenv("WORKER_REMOTE_IMAGE", "")
    spec = importlib.util.spec_from_file_location("isolated_remote_config", config.__file__)
    assert spec is not None and spec.loader is not None
    isolated_config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated_config)

    assert isolated_config.WORKER_REMOTE_IMAGE == ""

    monkeypatch.setenv("WORKER_REMOTE_IMAGE", "registry.example.net/ml/worker:stable")
    spec = importlib.util.spec_from_file_location("isolated_remote_config", config.__file__)
    assert spec is not None and spec.loader is not None
    isolated_config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated_config)
    assert isolated_config.WORKER_REMOTE_IMAGE == "registry.example.net/ml/worker:stable"


def test_compose_separates_local_worker_image_from_remote_default():
    """GPU 与 CPU Compose 默认把本机 worker 镜像标签用于远程流式部署。"""
    repository_root = Path(__file__).resolve().parents[2]
    for filename, local_default in (
        ("docker-compose.yml", "mtp-worker:local"),
        ("docker-compose.cpu.yml", "mtp-worker-cpu:local"),
    ):
        compose = (repository_root / filename).read_text(encoding="utf-8")
        assert f"WORKER_REMOTE_IMAGE: ${{WORKER_IMAGE:-{local_default}}}" in compose
        assert f"WORKER_IMAGE: ${{WORKER_IMAGE:-{local_default}}}" in compose
    remote_compose = (repository_root / "docker-compose.remote.yml").read_text(encoding="utf-8")
    assert "/var/run/docker.sock:/var/run/docker.sock" in remote_compose
    assert "USE_DATABASE: ${USE_DATABASE:-true}" in remote_compose


def test_local_docker_image_archive_uses_engine_api_version_and_tag_path(tmp_path, monkeypatch):
    """本机镜像归档从 Docker Engine API 读取，并按 API 版本安全编码镜像标签。"""
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    monkeypatch.setattr(config, "WORKER_DOCKER_SOCKET_PATH", str(socket_path))
    responses = [
        type("VersionResponse", (), {"status": 200, "read": lambda self: b'{"ApiVersion":"1.45"}', "close": lambda self: None})(),
        type("ImageResponse", (), {"status": 200, "read": lambda self, size=-1: b"image-tar", "close": lambda self: None})(),
    ]
    connections = []

    class FakeConnection:
        """模拟一个 HTTP over Unix socket 连接。"""

        def __init__(self, path, timeout=60.0):
            self.path = path
            self.timeout = timeout
            self.requests = []
            self.closed = False
            connections.append(self)

        def request(self, method, target):
            self.requests.append((method, target))

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            self.closed = True

    monkeypatch.setattr(remote_workers, "_DockerSocketHTTPConnection", FakeConnection)
    connection, response = remote_workers._open_local_docker_image_archive("registry.example/team/worker:tag")

    assert connection is connections[1]
    assert connections[0].requests == [("GET", "/version")]
    assert connections[1].requests == [("GET", "/v1.45/images/registry.example%2Fteam%2Fworker%3Atag/get")]
    assert response.read() == b"image-tar"
    connection.close()
    assert connection.closed


def test_local_docker_image_platform_reads_engine_image_metadata(tmp_path, monkeypatch):
    """通过 Docker Engine 镜像详情读取本地镜像平台，并编码镜像标签路径。"""
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    monkeypatch.setattr(config, "WORKER_DOCKER_SOCKET_PATH", str(socket_path))
    responses = [
        type("VersionResponse", (), {"status": 200, "read": lambda self: b'{"ApiVersion":"1.45"}', "close": lambda self: None})(),
        type(
            "ImageResponse",
            (),
            {
                "status": 200,
                "read": lambda self: b'{"Os":"linux","Architecture":"arm64"}',
                "close": lambda self: None,
            },
        )(),
    ]
    connections = []

    class FakeConnection:
        """记录镜像平台查询所发出的 Engine API 请求。"""

        def __init__(self, path, timeout=60.0):
            self.path = path
            self.requests = []
            connections.append(self)

        def request(self, method, target):
            self.requests.append((method, target))

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(remote_workers, "_DockerSocketHTTPConnection", FakeConnection)

    assert remote_workers._local_docker_image_platform("worker/team:tag") == "linux/arm64"
    assert connections[0].requests == [("GET", "/version")]
    assert connections[1].requests == [
        ("GET", "/v1.45/images/worker%2Fteam%3Atag/json")
    ]


def test_builds_worker_image_for_requested_platform_with_minimal_context(tmp_path, monkeypatch):
    """平台架构不同则可调用 Docker build API，并只上传镜像构建所需文件。"""
    import io
    import tarfile

    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    monkeypatch.setattr(config, "WORKER_DOCKER_SOCKET_PATH", str(socket_path))

    class FakeResponse:
        """按 Docker API 顺序返回版本和逐行构建结果。"""

        def __init__(self, status=200, body=b"", lines=()):
            self.status = status
            self.body = body
            self.lines = iter(lines)

        def read(self, *_args):
            return self.body

        def readline(self):
            return next(self.lines, b"")

        def close(self):
            pass

    responses = [
        FakeResponse(body=b'{"ApiVersion":"1.45"}'),
        FakeResponse(lines=[b'{"stream":"built\\n"}\n']),
    ]
    connections = []

    class FakeConnection:
        """记录 Docker Engine 构建请求和上下文归档。"""

        def __init__(self, path, timeout=60.0):
            self.path = path
            self.timeout = timeout
            self.requests = []
            connections.append(self)

        def request(self, method, target, body=None, headers=None):
            self.requests.append((method, target, body, headers))

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(remote_workers, "_DockerSocketHTTPConnection", FakeConnection)
    monkeypatch.setattr(
        remote_workers, "_local_docker_image_platform", lambda _image: "linux/amd64"
    )

    remote_workers._build_local_docker_image_for_platform("worker:local", "linux/x86_64")

    assert connections[0].requests[0][:2] == ("GET", "/version")
    method, target, body, headers = connections[1].requests[0]
    assert method == "POST"
    assert "platform=linux%2Famd64" in target
    assert "t=worker%3Alocal" in target
    assert headers == {"Content-Type": "application/x-tar"}
    assert body[:100].split(b"\0", 1)[0] == b"Dockerfile"
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as context:
        names = set(context.getnames())
        dockerfile = context.extractfile("Dockerfile").read().decode("utf-8")
    assert "Dockerfile" in names
    assert "requirements.txt" in names
    assert "app/remote_worker.sh" in names
    assert not any(name.startswith(("data/", "tests/")) for name in names)
    assert "dict(os.environ)" not in dockerfile
    assert "COPY app ./app" in dockerfile


def test_worker_code_archive_contains_only_runtime_app_files():
    """远程代码更新只打包 app 源码，不包含依赖或 Python 缓存。"""
    import io
    import tarfile

    with tarfile.open(
        fileobj=io.BytesIO(remote_workers._worker_code_archive()), mode="r:gz"
    ) as archive:
        names = archive.getnames()

    assert "app/worker.py" in names
    assert "requirements.txt" not in names
    assert not any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names)


def test_remote_worker_build_sends_minimal_context_and_quotes_docker_args(monkeypatch):
    """SSH 只接收精简源码 tar；目标机 Docker 命令安全引用镜像名并自行构建。"""
    import io
    import tarfile

    calls = []
    progress = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b"Build complete\n", stderr=b"")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    ssh_command = ["/usr/bin/ssh", "-p", "2222", "worker@compute.example.net", "bash -s"]
    remote_workers._build_worker_image_on_remote(
        "worker:stable;touch /tmp/pwn",
        "linux/x86_64",
        ssh_command,
        None,
        {"password": "", "passphrase": ""},
        _server(),
        timeout=3600,
        progress_callback=lambda phase, message: progress.append((phase, message)),
    )

    command, options = calls[0]
    assert command[-2] == "worker@compute.example.net"
    assert command[-1] == (
        "DOCKER_BUILDKIT=1 docker build --progress=plain --platform linux/amd64 "
        "--tag 'worker:stable;touch /tmp/pwn' -"
    )
    assert options["timeout"] == 3600
    assert any("MiB" in message and "目标机将拉取" in message for _phase, message in progress)
    assert options["input_data"][:100].split(b"\0", 1)[0] == b"Dockerfile"
    with tarfile.open(fileobj=io.BytesIO(options["input_data"]), mode="r:") as context:
        names = set(context.getnames())
    assert {"Dockerfile", "requirements.txt", "app/worker.py"} <= names
    assert not any(name.startswith(("data/", "tests/")) for name in names)


def test_remote_worker_build_falls_back_when_buildx_is_missing(monkeypatch):
    """Buildx 缺失时改用目标机原生架构和传统构建器重试。"""
    import io
    import tarfile

    calls = []
    progress = []
    missing_buildx = (
        b"ERROR: BuildKit is enabled but the buildx component is missing or broken.\n"
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=missing_buildx)
        return subprocess.CompletedProcess(command, 0, stdout=b"Build complete\n", stderr=b"")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    remote_workers._build_worker_image_on_remote(
        "worker:stable;touch /tmp/pwn",
        "linux/x86_64",
        ["/usr/bin/ssh", "worker@compute.example.net", "bash -s"],
        None,
        {"password": "", "passphrase": ""},
        _server(),
        timeout=3600,
        progress_callback=lambda phase, message: progress.append((phase, message)),
    )

    assert len(calls) == 2
    assert calls[0][0][-1] == (
        "DOCKER_BUILDKIT=1 docker build --progress=plain --platform linux/amd64 "
        "--tag 'worker:stable;touch /tmp/pwn' -"
    )
    assert calls[1][0][-1] == (
        "DOCKER_BUILDKIT=0 docker build --tag 'worker:stable;touch /tmp/pwn' -"
    )
    assert calls[1][1]["timeout"] == 3600
    with tarfile.open(fileobj=io.BytesIO(calls[0][1]["input_data"]), mode="r:") as context:
        buildkit_dockerfile = context.extractfile("Dockerfile").read().decode("utf-8")
    with tarfile.open(fileobj=io.BytesIO(calls[1][1]["input_data"]), mode="r:") as context:
        legacy_dockerfile = context.extractfile("Dockerfile").read().decode("utf-8")
    assert "RUN --mount=type=cache" in buildkit_dockerfile
    assert "RUN --mount=type=cache" not in legacy_dockerfile
    assert "RUN pip install --retries 10 -r requirements.txt" in legacy_dockerfile
    assert any("改用本机架构的兼容构建器重试" in message for _, message in progress)


def test_remote_worker_build_reports_redacted_target_error(monkeypatch):
    """目标 Docker 构建失败时保留原因并脱敏 SSH 密码。"""
    secret = "remote-ssh-secret"
    calls = []

    def failed_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 1, stdout=b"", stderr=f"pull failed for {secret}\n".encode()
        )

    monkeypatch.setattr(remote_workers, "_worker_build_context", lambda: b"minimal context")
    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", failed_run)

    with pytest.raises(remote_workers.RemoteWorkerError, match="pull failed") as exc_info:
        remote_workers._build_worker_image_on_remote(
            "worker:latest",
            "linux/amd64",
            ["ssh", "worker@compute.example.net", "bash -s"],
            None,
            {"password": secret, "passphrase": ""},
            _server(),
            timeout=3600,
        )

    assert secret not in str(exc_info.value)
    assert "[redacted]" in str(exc_info.value)
    assert len(calls) == 1


def test_remote_worker_build_reports_redacted_legacy_fallback_error(monkeypatch):
    """兼容构建器重试失败时保留脱敏后的最终错误。"""
    secret = "legacy-build-secret"
    calls = []

    def failed_run(command, **_kwargs):
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout=b"",
                stderr=(
                    b"BuildKit is enabled but the buildx component is missing or broken"
                ),
            )
        return subprocess.CompletedProcess(
            command, 1, stdout=b"", stderr=f"pull failed for {secret}\n".encode()
        )

    monkeypatch.setattr(remote_workers, "_worker_build_context", lambda **_kwargs: b"context")
    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", failed_run)

    with pytest.raises(remote_workers.RemoteWorkerError, match="pull failed") as exc_info:
        remote_workers._build_worker_image_on_remote(
            "worker:latest",
            "linux/amd64",
            ["ssh", "worker@compute.example.net", "bash -s"],
            None,
            {"password": secret, "passphrase": ""},
            _server(),
            timeout=3600,
        )

    assert secret not in str(exc_info.value)
    assert "[redacted]" in str(exc_info.value)
    assert len(calls) == 2
    assert "DOCKER_BUILDKIT=0" in calls[1][-1]


def test_remote_worker_build_rejects_unsupported_platform_before_ssh(monkeypatch):
    """远程 worker 构建在启动 SSH 前拒绝非 Linux 架构。"""
    monkeypatch.setattr(
        remote_workers,
        "_worker_build_context",
        lambda: pytest.fail("非法平台不应打包构建上下文"),
    )

    with pytest.raises(remote_workers.RemoteWorkerError, match="暂不支持目标镜像平台"):
        remote_workers._build_worker_image_on_remote(
            "worker:latest",
            "windows/amd64",
            ["ssh", "worker@compute.example.net", "bash -s"],
            None,
            {},
            _server(),
            timeout=3600,
        )


def test_worker_build_context_skips_invalid_same_named_dockerfile(tmp_path):
    """错误目录中的同名 Dockerfile 不得被上传给 Docker Engine。"""
    import io
    import tarfile

    invalid_root = tmp_path / "installed-package"
    invalid_root.mkdir()
    (invalid_root / "Dockerfile").write_text("ENV dict(os.environ)\n", encoding="utf-8")

    valid_root = tmp_path / "backend"
    (valid_root / "app").mkdir(parents=True)
    (valid_root / "static").mkdir()
    (valid_root / "sample_data").mkdir()
    (valid_root / "Dockerfile").write_text(
        "FROM python:3.12-slim\nCOPY app ./app\nCOPY Dockerfile ./Dockerfile\n",
        encoding="utf-8",
    )
    for filename in ("requirements.txt", "init_db.sh", "start.sh"):
        (valid_root / filename).write_text("", encoding="utf-8")

    context_bytes = remote_workers._worker_build_context([invalid_root, valid_root])
    with tarfile.open(fileobj=io.BytesIO(context_bytes), mode="r:") as context:
        dockerfile = context.extractfile("Dockerfile").read().decode("utf-8")

    assert dockerfile.startswith("FROM python:3.12-slim")
    assert "dict(os.environ)" not in dockerfile
    with pytest.raises(remote_workers.RemoteWorkerError, match="缓存挂载语法"):
        remote_workers._worker_build_context(
            [invalid_root, valid_root], legacy_builder=True
        )


def test_target_platform_worker_build_failure_is_reported(tmp_path, monkeypatch):
    """Docker Engine 构建错误会中止部署并保留可读原因。"""
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    monkeypatch.setattr(config, "WORKER_DOCKER_SOCKET_PATH", str(socket_path))
    class FakeResponse:
        """按 Docker API 顺序返回版本和构建失败事件。"""

        def __init__(self, body=b"", lines=()):
            self.status = 200
            self.body = body
            self.lines = iter(lines)

        def read(self, *_args):
            return self.body

        def readline(self):
            return next(self.lines, b"")

        def close(self):
            pass

    responses = [
        FakeResponse(body=b'{"ApiVersion":"1.45"}'),
        FakeResponse(lines=[b'{"error":"base image unavailable"}\n']),
    ]

    class FakeConnection:
        """为构建错误场景提供 Docker API 响应。"""

        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(remote_workers, "_DockerSocketHTTPConnection", FakeConnection)
    emitted = []

    with pytest.raises(remote_workers.RemoteWorkerError, match="base image unavailable"):
        remote_workers._build_local_docker_image_for_platform(
            "worker:local",
            "linux/amd64",
            progress_callback=lambda phase, message: emitted.append((phase, message)),
        )
    assert any("base image unavailable" in message for _phase, message in emitted)


def test_target_platform_worker_build_rejects_engine_http_errors(tmp_path, monkeypatch):
    """Docker Engine 拒绝构建请求时显示其错误响应，不继续部署。"""
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    monkeypatch.setattr(config, "WORKER_DOCKER_SOCKET_PATH", str(socket_path))

    class FakeResponse:
        def __init__(self, status, body=b""):
            self.status = status
            self.body = body

        def read(self, *_args):
            return self.body

        def close(self):
            pass

    responses = [
        FakeResponse(200, b'{"ApiVersion":"1.45"}'),
        FakeResponse(400, b"BuildKit is not available"),
    ]

    class FakeConnection:
        """返回 Docker Engine 构建接口拒绝响应。"""

        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(remote_workers, "_DockerSocketHTTPConnection", FakeConnection)

    with pytest.raises(remote_workers.RemoteWorkerError, match="BuildKit is not available"):
        remote_workers._build_local_docker_image_for_platform("worker:local", "linux/amd64")


def test_target_platform_build_rejects_non_linux_targets():
    """远程 worker 构建拒绝当前不支持的非 Linux 目标系统。"""
    with pytest.raises(remote_workers.RemoteWorkerError, match="暂不支持目标镜像平台"):
        remote_workers._build_local_docker_image_for_platform("worker:local", "windows/amd64")


def test_target_platform_worker_build_can_be_cancelled(tmp_path, monkeypatch):
    """中断远程操作时关闭等待中的 Docker 构建流，并阻止后续镜像传输。"""
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    monkeypatch.setattr(config, "WORKER_DOCKER_SOCKET_PATH", str(socket_path))
    read_started = threading.Event()
    release_response = threading.Event()
    cancellation = threading.Event()

    class VersionResponse:
        status = 200

        def read(self, *_args):
            return b'{"ApiVersion":"1.45"}'

        def close(self):
            pass

    class BuildResponse:
        status = 200

        def readline(self):
            read_started.set()
            release_response.wait(timeout=5)
            return b""

        def close(self):
            release_response.set()

    class FakeConnection:
        """让构建读取阻塞，验证取消路径会关闭 Docker 连接。"""

        def __init__(self, *_args, **_kwargs):
            self.is_build = False

        def request(self, method, *_args, **_kwargs):
            self.is_build = method == "POST"

        def getresponse(self):
            return BuildResponse() if self.is_build else VersionResponse()

        def close(self):
            if self.is_build:
                release_response.set()

    monkeypatch.setattr(remote_workers, "_DockerSocketHTTPConnection", FakeConnection)
    monkeypatch.setattr(
        remote_workers,
        "_remote_operation_cancel_requested",
        lambda operation_id: operation_id == "cancel-build" and cancellation.is_set(),
    )
    cancel_thread = threading.Thread(
        target=lambda: (read_started.wait(timeout=5), cancellation.set()), daemon=True
    )
    cancel_thread.start()

    with pytest.raises(remote_workers.RemoteWorkerCancelledError, match="操作已中断"):
        remote_workers._build_local_docker_image_for_platform(
            "worker:local", "linux/amd64", operation_id="cancel-build"
        )

    cancel_thread.join(timeout=5)
    assert not cancel_thread.is_alive()
    assert release_response.is_set()


def test_worker_image_archive_streams_over_ssh_without_saving_it(tmp_path, monkeypatch):
    """Docker 镜像归档按块经 SSH stdin 传输，不落地临时 tar 文件。"""
    archive = b"docker-image-archive-bytes" * 8192
    capture = tmp_path / "received-image.tar"
    fake_ssh = tmp_path / "ssh-receiver.sh"
    fake_ssh.write_text(
        "#!/bin/sh\ncat > \"$IMAGE_CAPTURE_PATH\"\nprintf 'Loaded image: worker:local\\n'\n",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("IMAGE_CAPTURE_PATH", str(capture))

    class FakeResponse:
        """提供分块读取的镜像归档响应体。"""

        def __init__(self):
            self.offset = 0
            self.closed = False

        def read(self, size=-1):
            if self.offset >= len(archive):
                return b""
            end = len(archive) if size < 0 else min(len(archive), self.offset + size)
            chunk = archive[self.offset:end]
            self.offset = end
            return chunk

        def close(self):
            self.closed = True

    class FakeConnection:
        """记录 Docker Engine HTTP 连接是否被正确关闭。"""

        closed = False

        def close(self):
            self.closed = True

    response = FakeResponse()
    connection = FakeConnection()
    monkeypatch.setattr(
        remote_workers, "_open_local_docker_image_archive", lambda _image: (connection, response)
    )

    result = remote_workers._stream_worker_image_to_remote(
        "worker:local", [str(fake_ssh), "docker load"], None, {}, _server(), timeout=30
    )

    assert result["stdout"].strip() == "Loaded image: worker:local"
    assert capture.read_bytes() == archive
    assert not list(tmp_path.glob("*.tar.tmp"))
    assert response.closed and connection.closed


def test_worker_image_transfer_failure_is_reported_and_redacted(tmp_path, monkeypatch):
    """远端 docker load 失败时返回脱敏错误，且关闭镜像源连接。"""
    fake_ssh = tmp_path / "ssh-receiver.sh"
    fake_ssh.write_text("#!/bin/sh\ncat >/dev/null\nprintf 'failed secret-value\\n' >&2\nexit 1\n", encoding="utf-8")
    fake_ssh.chmod(0o755)

    class FakeResponse:
        """返回最小镜像 tar 内容供传输失败用例消费。"""

        def __init__(self):
            self.sent = False
            self.closed = False

        def read(self, size=-1):
            if self.sent:
                return b""
            self.sent = True
            return b"image archive"

        def close(self):
            self.closed = True

    class FakeConnection:
        """记录失败路径是否释放 Docker Engine 连接。"""

        closed = False

        def close(self):
            self.closed = True

    response = FakeResponse()
    connection = FakeConnection()
    monkeypatch.setattr(
        remote_workers, "_open_local_docker_image_archive", lambda _image: (connection, response)
    )
    credentials = {"password": "secret-value", "passphrase": ""}

    with pytest.raises(remote_workers.RemoteWorkerError) as exc_info:
        remote_workers._stream_worker_image_to_remote(
            "worker:local", [str(fake_ssh), "docker load"], None, credentials, _server(), timeout=30
        )

    assert "secret-value" not in str(exc_info.value)
    assert "[redacted]" in str(exc_info.value)
    assert response.closed and connection.closed


def test_server_redis_override_is_redacted_from_remote_output(tmp_path, monkeypatch):
    """SSH 输出若意外回显服务器专属 Redis URL，API 错误和操作输出都必须脱敏。"""
    _remote_config(tmp_path, monkeypatch)
    secret_url = "rediss://worker:output-secret@remote-redis.example.net:6380/1"
    server = remote_workers.apply_remote_server_overrides(
        _server(), {"remote_redis_url": secret_url}
    )
    output = remote_workers._redact_output(f"redis={secret_url}", "", server=server)

    assert secret_url not in output["stdout"]
    assert "[redacted]" in output["stdout"]


def test_run_remote_action_trusts_host_key_once_and_keeps_secrets_out_of_argv(tmp_path, monkeypatch):
    """目标机通过 SSH 构建 worker 镜像，主机密钥固定且凭证不进入参数或日志。"""
    key, known_hosts = _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    calls = {}
    builds = []

    def fake_build(image, platform, command, environment, credentials, server, timeout,
                   operation_id=None, progress_callback=None):
        builds.append((image, platform, command, environment, credentials, server, timeout, operation_id))

    def fake_run(command, **kwargs):
        calls.setdefault("commands", []).append(command)
        if command[-1].startswith("docker info"):
            return subprocess.CompletedProcess(command, 0, stdout="linux/x86_64\n", stderr="")
        calls["command"] = command
        calls["input"] = kwargs["input_data"]
        calls["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(command, 0, stdout="worker running\n", stderr="")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    monkeypatch.setattr(remote_workers, "_build_worker_image_on_remote", fake_build)
    result = remote_workers.run_remote_action(_server(), "install")

    assert result["stdout"].strip() == "worker running"
    command = calls["command"]
    materialized_key = Path(command[command.index("-i") + 1])
    assert materialized_key.name.startswith("mtp-ssh-key-")
    assert not materialized_key.exists()
    assert command[command.index("-p") + 1] == "2222"
    assert "StrictHostKeyChecking=accept-new" in command
    assert f"UserKnownHostsFile={known_hosts}" in command
    assert known_hosts.parent.is_dir()
    assert command[-2:] == ["worker@compute.example.net", "bash -s"]
    assert calls["timeout"] == 3600
    assert calls["commands"][0][-1] == "docker info --format '{{.OSType}}/{{.Architecture}}'"
    assert len(builds) == 1
    assert builds[0][0:2] == ("registry.example.net/ml/worker:unit", "linux/amd64")
    assert builds[0][2][-2] == "worker@compute.example.net"
    assert builds[0][-2] == 3600
    assert not any("docker load" in " ".join(item) for item in calls["commands"])
    assert "secret-test" not in " ".join(command)
    assert "secret-test" in calls["input"]


def test_run_remote_action_builds_worker_on_target_before_install(tmp_path, monkeypatch):
    """目标平台收到精简构建上下文后自行构建镜像，再执行安装脚本。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(
        remote_workers,
        "_local_docker_image_platform",
        lambda _image: pytest.fail("远程安装不依赖平台本地 worker 镜像"),
    )
    calls = []
    builds = []
    steps = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[-1].startswith("docker info"):
            return subprocess.CompletedProcess(command, 0, stdout="linux/x86_64\n", stderr="")
        steps.append("install")
        return subprocess.CompletedProcess(command, 0, stdout="worker running\n", stderr="")

    def fake_build(image, platform, *_args, **_kwargs):
        builds.append((image, platform))
        steps.append("build")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    monkeypatch.setattr(remote_workers, "_build_worker_image_on_remote", fake_build)

    result = remote_workers.run_remote_action(_server(), "install")

    assert result["stdout"].strip() == "worker running"
    assert builds == [("registry.example.net/ml/worker:unit", "linux/amd64")]
    assert steps == ["build", "install"]
    assert len(calls) == 2
    assert calls[0][-1].startswith("docker info")


def test_run_remote_action_deploys_code_without_rebuilding_image(tmp_path, monkeypatch):
    """部署只传 app 源码，不查询架构、写配置或等待容器心跳。"""
    import base64
    import io
    import re
    import tarfile

    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(
        remote_workers,
        "_remote_docker_image_platform",
        lambda *_args, **_kwargs: pytest.fail("代码更新不应查询或重建 Docker 镜像"),
    )
    monkeypatch.setattr(
        remote_workers,
        "_build_worker_image_on_remote",
        lambda *_args, **_kwargs: pytest.fail("代码更新不应构建 Docker 镜像"),
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="worker updated\n", stderr="")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    operation_id = "c" * 32
    result = remote_workers.run_remote_action(_server(), "deploy", operation_id)

    assert result["stdout"] == "worker updated\n"
    assert len(calls) == 1
    command, options = calls[0]
    assert command[-1] == "bash -s"
    assert options["timeout"] == 180
    assert "export MTP_ACTION=deploy" in options["input_data"]
    encoded_archive = re.search(
        r"MTP_WORKER_APP_ARCHIVE_B64=\$\(cat <<'MTP_WORKER_APP_ARCHIVE'\n"
        r"([A-Za-z0-9+/=]+)\nMTP_WORKER_APP_ARCHIVE\n\)",
        options["input_data"],
    )
    assert encoded_archive
    archive_bytes = base64.b64decode(encoded_archive.group(1))
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        assert "app/worker.py" in archive.getnames()


def test_run_remote_action_config_overwrites_remote_config_without_code_archive(tmp_path, monkeypatch):
    """配置动作只发送配置脚本，不携带 worker 代码包。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="config applied\n", stderr="")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    result = remote_workers.run_remote_action(_server(), "config")

    assert result["stdout"] == "config applied\n"
    assert len(calls) == 1
    assert "export MTP_ACTION=config" in calls[0][1]["input_data"]
    assert "MTP_WORKER_APP_ARCHIVE_B64=$(cat" not in calls[0][1]["input_data"]


def test_run_remote_action_stops_when_remote_worker_build_fails(tmp_path, monkeypatch):
    """目标机镜像构建失败时不执行远端 worker 安装脚本。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    ssh_calls = []

    def fake_run(command, **_kwargs):
        ssh_calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="linux/amd64\n", stderr="")

    def failed_build(*_args, **_kwargs):
        raise remote_workers.RemoteWorkerError("目标机 Docker 构建 linux/amd64 worker 镜像失败")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    monkeypatch.setattr(remote_workers, "_build_worker_image_on_remote", failed_build)
    monkeypatch.setattr(
        remote_workers,
        "_stream_worker_image_to_remote",
        lambda *_args, **_kwargs: pytest.fail("构建失败后不应传输镜像"),
    )

    with pytest.raises(remote_workers.RemoteWorkerError, match="构建 linux/amd64"):
        remote_workers.run_remote_action(_server(), "install")

    assert len(ssh_calls) == 1
    assert ssh_calls[0][-1].startswith("docker info")


def test_run_remote_action_stops_when_remote_worker_build_is_cancelled(tmp_path, monkeypatch):
    """目标机构建期间取消操作后不再执行远程安装脚本。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    ssh_calls = []

    def fake_run(command, **_kwargs):
        ssh_calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="linux/amd64\n", stderr="")

    def cancelled_build(*_args, **_kwargs):
        raise remote_workers.RemoteWorkerCancelledError("操作已中断")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    monkeypatch.setattr(remote_workers, "_build_worker_image_on_remote", cancelled_build)

    with pytest.raises(remote_workers.RemoteWorkerCancelledError, match="操作已中断"):
        remote_workers.run_remote_action(_server(), "install")

    assert len(ssh_calls) == 1
    assert ssh_calls[0][-1].startswith("docker info")


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "message"),
    [
        (1, "", "docker access denied for secret-value", "access denied"),
        (0, "platform unavailable\n", "", "无效的平台标识"),
    ],
)
def test_remote_docker_platform_rejects_errors_and_redacts_credentials(
    monkeypatch, returncode, stdout, stderr, message
):
    """目标 Docker 平台检查拒绝失败或非法响应，错误详情不得泄露 SSH 密码。"""
    secret = "secret-value"
    credentials = {"auth_method": "password", "password": secret, "private_key": ""}
    command = ["ssh", "worker@compute.example.net", "bash -s"]
    monkeypatch.setattr(
        remote_workers,
        "_run_remote_subprocess",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            command, returncode, stdout=stdout, stderr=stderr
        ),
    )

    with pytest.raises(remote_workers.RemoteWorkerError, match=message) as exc_info:
        remote_workers._remote_docker_image_platform(
            command, None, credentials, _server(), timeout=120
        )

    assert secret not in str(exc_info.value)


def test_run_remote_action_uses_password_from_platform_askpass(tmp_path, monkeypatch):
    """页面密码走 OpenSSH askpass 环境，严格校验主机且不进入参数或日志。"""
    _, known_hosts = _remote_config(tmp_path, monkeypatch)
    password = "platform password ' with $(shell) text"
    _set_page_credential("password", password=password)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(
        remote_workers,
        "_stream_worker_image_to_remote",
        lambda *_args, **_kwargs: pytest.fail("start 操作不应重新传输镜像"),
    )
    calls = {}
    real_subprocess_run = subprocess.run

    def fake_run(command, **kwargs):
        calls["command"] = command
        calls["environment"] = kwargs["environment"]
        askpass = Path(kwargs["environment"]["SSH_ASKPASS"])
        calls["askpass_mode"] = stat.S_IMODE(askpass.stat().st_mode)
        prompt = real_subprocess_run(
            [str(askpass)], env=kwargs["environment"], capture_output=True, text=True
        )
        calls["prompt"] = prompt.stdout.rstrip("\n")
        return subprocess.CompletedProcess(command, 0, stdout=f"connected {password}", stderr="")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    result = remote_workers.run_remote_action(_server(), "start")

    command = calls["command"]
    assert "-i" not in command
    assert "BatchMode=no" in command
    assert "PasswordAuthentication=yes" in command
    assert "PubkeyAuthentication=no" in command
    assert "StrictHostKeyChecking=accept-new" in command
    assert f"UserKnownHostsFile={known_hosts}" in command
    assert password not in " ".join(command)
    assert calls["prompt"] == password
    assert calls["askpass_mode"] == 0o755
    assert "[redacted]" in result["stdout"]
    assert Path(calls["environment"]["SSH_ASKPASS"]).name == "ssh_askpass.sh"


def test_run_remote_action_redacts_password_from_ssh_error(tmp_path, monkeypatch):
    """SSH 登录失败信息即使回显凭证也应脱敏后返回。"""
    _remote_config(tmp_path, monkeypatch)
    password = "platform-secret-for-error-test"
    _set_page_credential("password", password=password)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 255, stdout="", stderr=f"SSH rejected credential {password}"
        )

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    with pytest.raises(remote_workers.RemoteWorkerError) as exc_info:
        remote_workers.run_remote_action(_server(), "start")

    assert password not in str(exc_info.value)
    assert "[redacted]" in str(exc_info.value)


def test_run_remote_action_uses_key_passphrase_without_exposing_it(tmp_path, monkeypatch):
    """加密私钥口令通过临时 askpass 辅助程序传递，不进入 SSH 参数。"""
    _remote_config(tmp_path, monkeypatch)
    passphrase = "key passphrase with spaces"
    _set_page_credential("key", private_key="-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----\n", passphrase=passphrase)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    calls = {}

    def fake_run(command, **kwargs):
        calls["command"] = command
        calls["environment"] = kwargs["environment"]
        return subprocess.CompletedProcess(command, 0, stdout="worker running", stderr="")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    remote_workers.run_remote_action(_server(), "start")

    assert "BatchMode=no" in calls["command"]
    assert passphrase not in " ".join(calls["command"])
    assert calls["environment"]["WORKER_SSH_ASKPASS_PASSPHRASE"] == passphrase
    assert Path(calls["environment"]["SSH_ASKPASS"]).name == "ssh_askpass.sh"


def test_run_remote_action_combines_key_and_password_with_separate_prompts(tmp_path, monkeypatch):
    """密钥 + 密码模式优先尝试密钥，且 askpass 能区分私钥口令和登录密码。"""
    _remote_config(tmp_path, monkeypatch)
    password = "combined login password"
    passphrase = "combined key passphrase"
    _set_page_credential(
        "key_password",
        private_key="-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----\n",
        password=password,
        passphrase=passphrase,
    )
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    real_subprocess_run = subprocess.run
    calls = {}

    def fake_run(command, **kwargs):
        calls["command"] = command
        environment = kwargs["environment"]
        askpass = environment["SSH_ASKPASS"]
        calls["passphrase_prompt"] = real_subprocess_run(
            [askpass, "Enter passphrase for key '/tmp/key':"],
            env=environment, capture_output=True, text=True,
        ).stdout.rstrip("\n")
        calls["password_prompt"] = real_subprocess_run(
            [askpass, "worker@compute.example.net's password:"],
            env=environment, capture_output=True, text=True,
        ).stdout.rstrip("\n")
        return subprocess.CompletedProcess(command, 0, stdout="connected", stderr="")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    remote_workers.run_remote_action(_server(), "start")

    command = calls["command"]
    assert "PreferredAuthentications=publickey,password" in command
    assert "PasswordAuthentication=yes" in command
    assert "BatchMode=no" in command
    assert calls["passphrase_prompt"] == passphrase
    assert calls["password_prompt"] == password
    assert password not in " ".join(command) and passphrase not in " ".join(command)


def test_remote_ssh_credentials_do_not_fall_back_to_environment(tmp_path, monkeypatch):
    """即使旧 SSH 密码环境变量存在，平台也必须要求页面凭证。"""
    from app import remote_config_repository

    _remote_config(tmp_path, monkeypatch)
    remote_config_repository.delete_ssh_credentials()
    monkeypatch.setenv("WORKER_SSH_PASSWORD", "legacy-environment-secret")
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")

    errors = remote_workers.remote_configuration_errors()
    assert any("页面配置 SSH 凭证" in item for item in errors)
    with pytest.raises(remote_workers.RemoteWorkerError, match="页面配置 SSH 凭证"):
        remote_workers.run_remote_action(_server(), "start")


def test_run_remote_action_uses_page_password_and_redacts_it(tmp_path, monkeypatch, request):
    """页面保存的密码解密后通过 askpass 使用，且 SSH 输出不会泄漏密码。"""
    from cryptography.fernet import Fernet

    _remote_config(tmp_path, monkeypatch)
    secret = "page-managed password with $ and spaces"
    monkeypatch.setattr(config, "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(manager, "distributed", True)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    manager.lease.delete_remote_ssh_credentials()
    request.addfinalizer(manager.lease.delete_remote_ssh_credentials)
    _set_page_credential("password", password=secret)
    calls = {}

    def fake_run(command, **kwargs):
        calls["command"] = command
        calls["environment"] = kwargs["environment"]
        return subprocess.CompletedProcess(command, 255, stdout="", stderr=f"rejected {secret}")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    with pytest.raises(remote_workers.RemoteWorkerError) as exc_info:
        remote_workers.run_remote_action(_server(), "start")

    assert "-i" not in calls["command"]
    assert secret not in " ".join(calls["command"])
    assert calls["environment"]["WORKER_SSH_ASKPASS_PASSWORD"] == secret
    assert secret not in str(exc_info.value)
    assert "[redacted]" in str(exc_info.value)
    manager.lease.delete_remote_ssh_credentials()


def test_run_remote_action_materializes_page_private_key_temporarily(tmp_path, monkeypatch, request):
    """页面密钥作为 600 权限临时文件使用，SSH 完成后立即清理。"""
    from cryptography.fernet import Fernet

    _remote_config(tmp_path, monkeypatch)
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        pytest.skip("测试环境没有 ssh-keygen")
    key_path = tmp_path / "uploaded_key"
    subprocess.run(
        [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    private_key = key_path.read_text(encoding="utf-8")
    monkeypatch.setattr(config, "WORKER_SSH_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(manager, "distributed", True)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    manager.lease.delete_remote_ssh_credentials()
    request.addfinalizer(manager.lease.delete_remote_ssh_credentials)
    _set_page_credential("key", private_key=private_key)
    calls = {}

    def fake_run(command, **kwargs):
        temporary_key = Path(command[command.index("-i") + 1])
        calls["key_path"] = temporary_key
        calls["key_text"] = temporary_key.read_text(encoding="utf-8")
        calls["key_mode"] = stat.S_IMODE(temporary_key.stat().st_mode)
        return subprocess.CompletedProcess(command, 0, stdout="worker running", stderr="")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
    remote_workers.run_remote_action(_server(), "start")

    assert calls["key_text"] == private_key
    assert calls["key_mode"] == 0o600
    assert not calls["key_path"].exists()
    manager.lease.delete_remote_ssh_credentials()


def test_run_remote_action_brackets_ipv6_ssh_destination(tmp_path, monkeypatch):
    """IPv6 目标主机以 OpenSSH 支持的方括号形式构造连接目标。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    calls = {}

    def fake_run(command, **kwargs):
        calls["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(remote_workers, "_run_remote_subprocess", fake_run)
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


def test_execute_remote_operation_persists_success_and_failure(monkeypatch, request):
    """远程操作状态留在协调存储，服务器最终状态持久化到 MySQL。"""
    from app import remote_config_repository

    monkeypatch.setattr(config, "USE_DATABASE", True)
    monkeypatch.setattr(
        config,
        "WORKER_REMOTE_REDIS_URL",
        "rediss://:redis-secret@redis.example.net:6380/0",
    )
    machine_id = "remote-operation-test"
    operation_id = "operation-test"
    server = _server(machine_id=machine_id, deployed=True)
    request.addfinalizer(lambda: remote_config_repository.delete_remote_server(machine_id))
    remote_config_repository.save_remote_server(server)
    manager.lease.set_remote_operation(
        operation_id,
        {"operation_id": operation_id, "machine_id": machine_id, "action": "start", "status": "queued"},
    )
    monkeypatch.setattr(
        remote_workers,
        "run_remote_action",
        lambda *_args: {
            "stdout": "worker 配置摘要：\nWORKER_MODE=cpu\nS3_SECRET_KEY=[已配置]",
            "stderr": "worker boot warning",
        },
    )

    remote_workers.execute_remote_operation(operation_id, server, "start")
    result = manager.lease.get_remote_operation(operation_id)
    assert result["status"] == "completed"
    assert "worker 配置摘要" in result["output"]
    assert "worker 配置摘要" in result["log"]
    assert "S3_SECRET_KEY=[已配置]" in result["log"]
    assert result["stderr"] == "worker boot warning"
    assert result["phase"] == "操作完成"
    assert result["duration_sec"] >= 0
    assert "[远程服务器启动配置]" in result["log"]
    assert '"action":"start"' in result["log"]
    assert '"mode":"cpu"' in result["log"]
    assert "redis.example.net:6380/0" in result["log"]
    assert "redis-secret" not in result["log"]
    assert "远程操作已成功完成" in result["log"]
    assert remote_config_repository.get_remote_server(machine_id)["status"] == "online"
    assert remote_config_repository.get_remote_server(machine_id)["deployed"] is True

    def fail_remote(*_args):
        raise remote_workers.RemoteWorkerError("SSH 登录失败")

    manager.lease.set_remote_operation(operation_id, {"operation_id": operation_id, "status": "queued"})
    monkeypatch.setattr(remote_workers, "run_remote_action", fail_remote)
    remote_workers.execute_remote_operation(operation_id, server, "stop")
    result = manager.lease.get_remote_operation(operation_id)
    assert result["status"] == "failed"
    assert result["error"] == "SSH 登录失败"
    assert "SSH 登录失败" in result["log"]
    assert remote_config_repository.get_remote_server(machine_id)["status"] == "error"
    assert remote_config_repository.get_remote_server(machine_id)["deployed"] is True


def test_remote_subprocess_stops_when_operation_is_cancelled():
    """中断信号会终止正在等待的本机 SSH 子进程并返回已中断状态。"""
    operation_id = "a" * 32
    manager.lease.clear_remote_operation_cancel(operation_id)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    manager.lease.request_remote_operation_cancel(operation_id)
    started_at = time.monotonic()
    try:
        with pytest.raises(remote_workers.RemoteWorkerCancelledError, match="远端可能已有部分变更"):
            remote_workers._communicate_remote_process(process, operation_id, timeout=30)
        assert process.poll() is not None
        assert time.monotonic() - started_at < 5
    finally:
        manager.lease.clear_remote_operation_cancel(operation_id)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_execute_remote_operation_honors_cancel_before_ssh(monkeypatch):
    """排队阶段收到中断时不启动 SSH，并将操作记录收敛为已中断。"""
    operation_id = "b" * 32
    server = _server(machine_id="remote-cancel-before-start")
    manager.lease.set_remote_operation(
        operation_id,
        {"operation_id": operation_id, "machine_id": server["machine_id"], "action": "start", "status": "queued"},
    )
    manager.lease.request_remote_operation_cancel(operation_id)
    monkeypatch.setattr(remote_workers.remote_config_repository, "save_remote_server", lambda _server: None)
    monkeypatch.setattr(
        remote_workers,
        "run_remote_action",
        lambda *_args: pytest.fail("排队操作取消后不应启动 SSH"),
    )

    remote_workers.execute_remote_operation(operation_id, server, "start")

    result = manager.lease.get_remote_operation(operation_id)
    assert result["status"] == "cancelled"
    assert "部分变更" in result["error"]
    assert manager.lease.is_remote_operation_cancel_requested(operation_id) is False
    assert remote_workers.is_remote_operation_active(operation_id) is False


def test_execute_remote_operation_records_running_cancel(monkeypatch):
    """执行期间收到中断时保存 cancelled 终态并标记服务器状态待检查。"""
    operation_id = "c" * 32
    server = _server(machine_id="remote-cancel-running")
    manager.lease.set_remote_operation(
        operation_id,
        {"operation_id": operation_id, "machine_id": server["machine_id"], "action": "restart", "status": "queued"},
    )
    monkeypatch.setattr(remote_workers.remote_config_repository, "save_remote_server", lambda _server: None)

    def cancel_during_remote_action(_server, _action, current_operation_id):
        manager.lease.request_remote_operation_cancel(current_operation_id)
        raise remote_workers.RemoteWorkerCancelledError("操作已中断；远端可能已有部分变更")

    monkeypatch.setattr(remote_workers, "run_remote_action", cancel_during_remote_action)
    remote_workers.execute_remote_operation(operation_id, server, "restart")

    result = manager.lease.get_remote_operation(operation_id)
    assert result["status"] == "cancelled"
    assert server["status"] == "error"
    assert manager.lease.is_remote_operation_cancel_requested(operation_id) is False


def test_remote_bootstrap_script_installs_cpu_and_gpu_workers(tmp_path: Path):
    """远程单文件脚本验证已加载镜像、写私有配置并按 CPU/GPU 模式启动容器。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "if [[ \"$1 $2\" == 'image inspect' && \"${DOCKER_MISSING:-false}\" == true ]]; then exit 1; fi\n"
        "if [[ \"$1\" == inspect && \"${DOCKER_MISSING:-false}\" == true ]]; then exit 1; fi\n"
        "if [[ \"$1\" == inspect ]]; then printf 'state=running restart_count=0 exit_code=0 error=\\n'; fi\n"
        "if [[ \"$1\" == logs ]]; then printf 'worker startup diagnostic\\n'; fi\n"
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
            "MTP_REDIS_PASSWORD": "redis-password-test",
            "MTP_S3_ENDPOINT": "storage.example.net:9000",
            "MTP_S3_ACCESS_KEY": "access-test",
            "MTP_S3_SECRET_KEY": "secret-test",
            "MTP_S3_BUCKET": "models-test",
            "MTP_S3_SECURE": "true",
            "MTP_WORKER_APP_ARCHIVE_B64": _test_worker_app_archive_base64(),
        })
        result = subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)

        assert result.returncode == 0, result.stderr
        assert "worker 容器启动快照" in result.stdout
        assert "restart_count=" in result.stdout
        assert "worker startup diagnostic" in result.stdout
        env_file = home / ".config" / "mtp-worker" / f"remote-{mode}.env"
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
        saved = env_file.read_text(encoding="utf-8")
        assert "WORKER_MANAGED=true" in saved
        assert "S3_SECRET_KEY=secret-test" in saved
        assert "REDIS_PASSWORD=redis-password-test" in saved
        app_dir = home / ".config" / "mtp-worker" / f"remote-{mode}" / "app"
        assert (app_dir / "worker.py").is_file()
        commands = docker_log.read_text(encoding="utf-8")
        assert "image inspect registry.example.net/ml/worker:unit" in commands
        assert "pull registry.example.net/ml/worker:unit" not in commands
        assert "git clone" not in commands
        assert "inspect --format state={{.State.Status}}" in commands
        assert "logs --tail=80" in commands
        assert f"{app_dir}:/app/app:ro" in commands
        assert ("--gpus all" in commands) is expected_gpu
        assert "mtp-worker-remote-" in commands


def test_remote_bootstrap_script_config_requires_existing_container(tmp_path: Path):
    """没有现有容器时，配置动作覆盖环境文件后报错，提示先部署。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == inspect && \"$2\" == \"mtp-worker-remote-config\" ]]; then exit 1; fi\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env_file = home / ".config" / "mtp-worker" / "remote-config.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("S3_ENDPOINT=old-storage.example.net:9000\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        "HOME": str(home),
        "DOCKER_LOG": str(docker_log),
        "MTP_ACTION": "config",
        "MTP_MACHINE_ID": "remote-config",
        "MTP_MODE": "cpu",
        "MTP_CPU_SLOTS": "4",
        "MTP_WORKER_IMAGE": "registry.example.net/ml/worker:unit",
        "MTP_REDIS_URL": "rediss://redis.example.net:6380/0",
        "MTP_S3_ENDPOINT": "storage.example.net:9000",
        "MTP_S3_ACCESS_KEY": "access-test",
        "MTP_S3_SECRET_KEY": "secret-test",
        "MTP_S3_BUCKET": "models-test",
        "MTP_S3_SECURE": "true",
    })
    script = Path(remote_workers.__file__).with_name("remote_worker.sh")

    result = subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)

    assert result.returncode == 4
    assert "未找到 worker 容器" in result.stderr
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    saved = env_file.read_text(encoding="utf-8")
    assert "S3_ENDPOINT=storage.example.net:9000" in saved
    assert "WORKER_CPU_SLOTS=4" in saved
    assert not (home / ".config" / "mtp-worker" / "remote-config" / "app").exists()
    assert not docker_log.exists()


def test_remote_bootstrap_script_config_recreates_container_with_latest_env(tmp_path: Path):
    """已有容器时，配置动作覆盖环境文件并重建容器应用新配置。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == inspect && \"$2\" == \"mtp-worker-remote-config\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == inspect && \"$2\" == \"mtp-worker-remote-config-previous\" ]]; then exit 1; fi\n"
        "if [[ \"$1\" == inspect && \"$2\" == --format ]]; then\n"
        "  if [[ \"$3\" == \"{{.Image}}\" ]]; then printf 'sha256:existing-image\\n'; elif [[ \"$3\" == \"{{.State.Status}}\" ]]; then printf 'running\\n'; else printf 'state=running restart_count=0 exit_code=0 error=\\n'; fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$1\" == logs ]]; then printf 'worker startup diagnostic\\n'; exit 0; fi\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "if [[ \"$1\" == run ]]; then\n"
        "  for ((i=1; i<=$#; i++)); do\n"
        "    if [[ \"${!i}\" == --env-file ]]; then next=$((i + 1)); printf 'env=%s\\n' \"$(cat \"${!next}\")\" >> \"$DOCKER_LOG\"; fi\n"
        "  done\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env_file = home / ".config" / "mtp-worker" / "remote-config.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("S3_ENDPOINT=old-storage.example.net:9000\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        "HOME": str(home),
        "DOCKER_LOG": str(docker_log),
        "MTP_ACTION": "config",
        "MTP_MACHINE_ID": "remote-config",
        "MTP_MODE": "cpu",
        "MTP_CPU_SLOTS": "4",
        "MTP_WORKER_IMAGE": "registry.example.net/ml/worker:unit",
        "MTP_REDIS_URL": "rediss://redis.example.net:6380/0",
        "MTP_S3_ENDPOINT": "storage.example.net:9000",
        "MTP_S3_ACCESS_KEY": "access-test",
        "MTP_S3_SECRET_KEY": "secret-test",
        "MTP_S3_BUCKET": "models-test",
        "MTP_S3_SECURE": "true",
    })
    script = Path(remote_workers.__file__).with_name("remote_worker.sh")

    result = subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)

    assert result.returncode == 0, result.stderr
    assert "S3_ENDPOINT=storage.example.net:9000" in env_file.read_text(encoding="utf-8")
    commands = docker_log.read_text(encoding="utf-8")
    assert "stop --time 45 mtp-worker-remote-config" in commands
    assert "rename mtp-worker-remote-config mtp-worker-remote-config-previous" in commands
    assert "run --detach" in commands
    assert "S3_ENDPOINT=storage.example.net:9000" in commands
    assert "rm --force mtp-worker-remote-config-previous" in commands
    assert "worker 配置已更新并应用" in result.stdout


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("start", "start mtp-worker-remote-config"),
        ("restart", "restart --time 45 mtp-worker-remote-config"),
    ],
)
def test_remote_bootstrap_script_lifecycle_controls_container_only(
    tmp_path: Path, action: str, expected: str
):
    """启动或重启只操作现有容器，不重建容器也不改动配置文件。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == inspect && \"$2\" == \"mtp-worker-remote-config\" ]]; then exit 0; fi\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env_file = home / ".config" / "mtp-worker" / "remote-config.env"
    env_file.parent.mkdir(parents=True)
    latest_env = "S3_ENDPOINT=latest-storage.example.net:9000\n"
    env_file.write_text(latest_env, encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        "HOME": str(home),
        "DOCKER_LOG": str(docker_log),
        "MTP_ACTION": action,
        "MTP_MACHINE_ID": "remote-config",
        "MTP_MODE": "cpu",
        "MTP_CPU_SLOTS": "4",
        "MTP_WORKER_IMAGE": "registry.example.net/ml/worker:unit",
        "MTP_REDIS_URL": "rediss://redis.example.net:6380/0",
        "MTP_S3_ENDPOINT": "storage.example.net:9000",
        "MTP_S3_ACCESS_KEY": "access-test",
        "MTP_S3_SECRET_KEY": "secret-test",
        "MTP_S3_BUCKET": "models-test",
        "MTP_S3_SECURE": "true",
    })
    script = Path(remote_workers.__file__).with_name("remote_worker.sh")

    result = subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)

    assert result.returncode == 0, result.stderr
    assert env_file.read_text(encoding="utf-8") == latest_env
    commands = docker_log.read_text(encoding="utf-8")
    assert expected in commands
    assert "run --detach" not in commands
    assert "--env-file" not in commands


def test_remote_bootstrap_script_config_rolls_back_when_new_container_fails(tmp_path: Path):
    """更新配置重建容器失败时恢复旧容器，并保留新配置供下次重试。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == inspect && \"$2\" == \"mtp-worker-remote-config\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == inspect && \"$2\" == \"mtp-worker-remote-config-previous\" ]]; then exit 1; fi\n"
        "if [[ \"$1\" == inspect && \"$2\" == --format ]]; then\n"
        "  if [[ \"$3\" == \"{{.Image}}\" ]]; then printf 'sha256:existing-image\\n'; elif [[ \"$3\" == \"{{.State.Status}}\" ]]; then printf 'running\\n'; fi\n"
        "  exit 0\n"
        "fi\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "if [[ \"$1\" == run ]]; then exit 1; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env_file = home / ".config" / "mtp-worker" / "remote-config.env"
    env_file.parent.mkdir(parents=True)
    latest_env = "S3_ENDPOINT=latest-storage.example.net:9000\n"
    env_file.write_text(latest_env, encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        "HOME": str(home),
        "DOCKER_LOG": str(docker_log),
        "MTP_ACTION": "config",
        "MTP_MACHINE_ID": "remote-config",
        "MTP_MODE": "cpu",
        "MTP_CPU_SLOTS": "4",
        "MTP_WORKER_IMAGE": "registry.example.net/ml/worker:unit",
        "MTP_REDIS_URL": "rediss://redis.example.net:6380/0",
        "MTP_S3_ENDPOINT": "storage.example.net:9000",
        "MTP_S3_ACCESS_KEY": "access-test",
        "MTP_S3_SECRET_KEY": "secret-test",
        "MTP_S3_BUCKET": "models-test",
        "MTP_S3_SECURE": "true",
    })
    script = Path(remote_workers.__file__).with_name("remote_worker.sh")

    result = subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)

    assert result.returncode == 1
    assert "S3_ENDPOINT=storage.example.net:9000" in env_file.read_text(encoding="utf-8")
    commands = docker_log.read_text(encoding="utf-8")
    assert "rename mtp-worker-remote-config mtp-worker-remote-config-previous" in commands
    assert "rename mtp-worker-remote-config-previous mtp-worker-remote-config" in commands
    assert "start mtp-worker-remote-config" in commands
    assert "已恢复旧容器" in result.stderr


def test_remote_bootstrap_script_deploy_syncs_code_and_recreates_container(tmp_path: Path):
    """部署同步 worker 源码、重写配置并用现有镜像重建容器。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == inspect && \"$2\" == \"mtp-worker-remote-deploy\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == inspect && \"$2\" == \"mtp-worker-remote-deploy-previous\" ]]; then exit 1; fi\n"
        "if [[ \"$1\" == inspect && \"$2\" == --format ]]; then\n"
        "  if [[ \"$3\" == \"{{.Image}}\" ]]; then printf 'sha256:existing-image\\n'; elif [[ \"$3\" == \"{{.State.Status}}\" ]]; then printf 'running\\n'; else printf 'state=running restart_count=0 exit_code=0 error=\\n'; fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$1\" == logs ]]; then printf 'worker startup diagnostic\\n'; exit 0; fi\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env_file = home / ".config" / "mtp-worker" / "remote-deploy.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("S3_ENDPOINT=old-storage.example.net:9000\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        "HOME": str(home),
        "DOCKER_LOG": str(docker_log),
        "MTP_ACTION": "deploy",
        "MTP_MACHINE_ID": "remote-deploy",
        "MTP_MODE": "cpu",
        "MTP_CPU_SLOTS": "4",
        "MTP_WORKER_IMAGE": "registry.example.net/ml/worker:unit",
        "MTP_REDIS_URL": "rediss://redis.example.net:6380/0",
        "MTP_S3_ENDPOINT": "storage.example.net:9000",
        "MTP_S3_ACCESS_KEY": "access-test",
        "MTP_S3_SECRET_KEY": "secret-test",
        "MTP_S3_BUCKET": "models-test",
        "MTP_S3_SECURE": "true",
        "MTP_WORKER_APP_ARCHIVE_B64": _test_worker_app_archive_base64(),
    })
    script = Path(remote_workers.__file__).with_name("remote_worker.sh")

    result = subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)

    assert result.returncode == 0, result.stderr
    assert "worker 代码与配置已部署并重建容器" in result.stdout
    saved = env_file.read_text(encoding="utf-8")
    assert "S3_ENDPOINT=storage.example.net:9000" in saved
    assert "WORKER_CPU_SLOTS=4" in saved
    assert (home / ".config" / "mtp-worker" / "remote-deploy" / "app" / "worker.py").is_file()
    commands = docker_log.read_text(encoding="utf-8")
    assert "stop --time 45 mtp-worker-remote-deploy" in commands
    assert "rename mtp-worker-remote-deploy mtp-worker-remote-deploy-previous" in commands
    assert "run --detach" in commands
    assert "rm --force mtp-worker-remote-deploy-previous" in commands


def test_remote_bootstrap_script_config_keeps_stopped_container_stopped(tmp_path: Path):
    """容器处于停止状态时，更新配置重建容器但不启动它。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == inspect && \"$2\" == \"mtp-worker-remote-config\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == inspect && \"$2\" == \"mtp-worker-remote-config-previous\" ]]; then exit 1; fi\n"
        "if [[ \"$1\" == inspect && \"$2\" == --format ]]; then\n"
        "  if [[ \"$3\" == \"{{.Image}}\" ]]; then printf 'sha256:existing-image\\n'; elif [[ \"$3\" == \"{{.State.Status}}\" ]]; then printf 'exited\\n'; else printf 'state=exited restart_count=0 exit_code=0 error=\\n'; fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$1\" == logs ]]; then printf 'worker startup diagnostic\\n'; exit 0; fi\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env_file = home / ".config" / "mtp-worker" / "remote-config.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("S3_ENDPOINT=old-storage.example.net:9000\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        "HOME": str(home),
        "DOCKER_LOG": str(docker_log),
        "MTP_ACTION": "config",
        "MTP_MACHINE_ID": "remote-config",
        "MTP_MODE": "cpu",
        "MTP_CPU_SLOTS": "4",
        "MTP_WORKER_IMAGE": "registry.example.net/ml/worker:unit",
        "MTP_REDIS_URL": "rediss://redis.example.net:6380/0",
        "MTP_S3_ENDPOINT": "storage.example.net:9000",
        "MTP_S3_ACCESS_KEY": "access-test",
        "MTP_S3_SECRET_KEY": "secret-test",
        "MTP_S3_BUCKET": "models-test",
        "MTP_S3_SECURE": "true",
    })
    script = Path(remote_workers.__file__).with_name("remote_worker.sh")

    result = subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)

    assert result.returncode == 0, result.stderr
    assert "保持原停止状态" in result.stdout
    commands = docker_log.read_text(encoding="utf-8")
    assert "create --name mtp-worker-remote-config" in commands
    assert "run --detach" not in commands
    assert "stop --time" not in commands
    assert "start mtp-worker-remote-config" not in commands


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("stop", "stop --time 45 mtp-worker-remote-control"),
        ("status", "inspect --format {{.State.Status}} mtp-worker-remote-control"),
    ],
)
def test_remote_bootstrap_script_lifecycle(tmp_path: Path, action: str, expected: str):
    """停止和状态查询只操作指定 worker 容器，并保留优雅停止时间。"""
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
