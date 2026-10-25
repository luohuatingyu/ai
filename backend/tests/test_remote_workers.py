"""远程 worker 安全、SSH 命令构造、异步操作状态和单文件脚本测试。"""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import app.config as config
import app.remote_workers as remote_workers
from app.state import manager


@pytest.fixture(autouse=True)
def _clear_page_credentials_after_test():
    """每个用例结束后清除测试写入的共享 SSH 凭证密文。"""
    yield
    manager.lease.delete_remote_ssh_credentials()


def _remote_config(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """配置远程执行环境并创建隔离的页面凭证，不读取真实凭证。"""
    from cryptography.fernet import Fernet
    key = tmp_path / "worker_key"
    key.write_text("test-key", encoding="utf-8")
    key.chmod(0o600)
    known_hosts = tmp_path / "remote_known_hosts"
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


def _set_page_credential(auth_method: str, **overrides) -> dict:
    """替换测试用默认页面凭证，覆盖三类认证组合。"""
    manager.lease.delete_remote_ssh_credentials()
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


def test_remote_configuration_requires_page_credentials_and_local_image_tag(tmp_path, monkeypatch):
    """远程执行依赖页面凭证和平台本地镜像标签，不要求镜像仓库地址。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    assert remote_workers.remote_configuration_errors() == []

    missing_key = {"auth_method": "key", "private_key": "", "password": "", "passphrase": ""}
    assert any("页面未配置 SSH 私钥" in item for item in remote_workers.remote_configuration_errors(missing_key))
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "")
    assert any("平台本地 Docker 镜像标签" in item for item in remote_workers.remote_configuration_errors())
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "worker:latest")
    assert not any("镜像" in item for item in remote_workers.remote_configuration_errors())
    monkeypatch.setattr(config, "WORKER_REMOTE_ENABLED", False)
    assert any("尚未启用" in item for item in remote_workers.remote_configuration_errors())


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


def test_remote_deployment_settings_apply_per_server_overrides_and_platform_fallback(tmp_path, monkeypatch):
    """服务器覆盖值优先，未覆盖的配置回退到平台 WORKER_IMAGE、REDIS_URL 和 S3_ENDPOINT。"""
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
        "s3_endpoint": "platform-s3.example.net:9000",
    }

    redis_url = "rediss://worker:private-pass@remote-redis.example.net:6380/4"
    server = remote_workers.apply_remote_server_overrides(
        _server(),
        {
            "worker_image": "registry.example.net/remote/worker:v5",
            "remote_redis_url": redis_url,
            "remote_s3_endpoint": "remote-s3.example.net:9000",
        },
    )
    assert server["remote_redis_url_encrypted"] != redis_url
    assert "remote_redis_url" not in server
    assert remote_workers.remote_deployment_settings(server) == {
        "worker_image": "registry.example.net/remote/worker:v5",
        "redis_url": redis_url,
        "s3_endpoint": "remote-s3.example.net:9000",
    }
    public = remote_workers.remote_server_public_view(server)
    assert public["has_remote_redis_url_override"] is True
    assert "remote_redis_url_encrypted" not in public
    assert redis_url not in repr(public)


def test_remote_configuration_accepts_complete_server_overrides_without_remote_env_values(tmp_path, monkeypatch):
    """远程配置可由单机覆盖值补齐，避免要求 WORKER_REMOTE_* 重复配置。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(config, "WORKER_REMOTE_IMAGE", "")
    monkeypatch.setattr(config, "WORKER_REMOTE_REDIS_URL", "")
    monkeypatch.setattr(config, "WORKER_REMOTE_S3_ENDPOINT", "")
    server = remote_workers.apply_remote_server_overrides(
        _server(),
        {
            "worker_image": "registry.example.net/server/worker:v1",
            "remote_redis_url": "rediss://redis.example.net:6380/0",
            "remote_s3_endpoint": "s3.example.net:9000",
        },
    )

    assert remote_workers.remote_configuration_errors(server=server) == []
    rendered = remote_workers.render_remote_script(server, "install")
    assert "export MTP_WORKER_IMAGE=registry.example.net/server/worker:v1" in rendered
    assert "export MTP_REDIS_URL=rediss://redis.example.net:6380/0" in rendered
    assert "export MTP_S3_ENDPOINT=s3.example.net:9000" in rendered


def test_remote_server_redis_override_can_be_preserved_replaced_or_cleared(tmp_path, monkeypatch):
    """编辑时空 Redis 输入保留密文，重置选项清除覆盖值并回退到平台默认。"""
    _remote_config(tmp_path, monkeypatch)
    original = "redis://user:first-password@redis-one.example.net:6379/0"
    replacement = "rediss://user:second-password@redis-two.example.net:6380/2"
    server = remote_workers.apply_remote_server_overrides(
        _server(), {"remote_redis_url": original}
    )
    encrypted_original = server["remote_redis_url_encrypted"]

    preserved = remote_workers.apply_remote_server_overrides(
        server, {"remote_redis_url": ""}, is_update=True
    )
    assert preserved["remote_redis_url_encrypted"] == encrypted_original
    replaced = remote_workers.apply_remote_server_overrides(
        preserved, {"remote_redis_url": replacement}, is_update=True
    )
    assert remote_workers.remote_deployment_settings(replaced)["redis_url"] == replacement
    cleared = remote_workers.apply_remote_server_overrides(
        replaced, {"clear_remote_redis_url_override": True}, is_update=True
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
    """安装前经 SSH 传输本机镜像，主机密钥固定且凭证不进入参数或日志。"""
    key, known_hosts = _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(remote_workers, "_local_docker_image_platform", lambda _image: "linux/amd64")
    calls = {}
    transfers = []

    def fake_transfer(image, command, environment, credentials, server, timeout):
        transfers.append((image, command, environment, credentials, server, timeout))
        return {"stdout": "Loaded image: worker:local", "stderr": ""}

    def fake_run(command, **kwargs):
        calls.setdefault("commands", []).append(command)
        if command[-1].startswith("docker info"):
            return subprocess.CompletedProcess(command, 0, stdout="linux/x86_64\n", stderr="")
        calls["command"] = command
        calls["input"] = kwargs["input"]
        calls["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(command, 0, stdout="worker running\n", stderr="")

    monkeypatch.setattr(remote_workers.subprocess, "run", fake_run)
    monkeypatch.setattr(remote_workers, "_stream_worker_image_to_remote", fake_transfer)
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
    assert len(transfers) == 1
    assert transfers[0][0] == "registry.example.net/ml/worker:unit"
    assert transfers[0][1][-2:] == ["worker@compute.example.net", "docker load"]
    assert transfers[0][-1] == 3600
    assert "secret-test" not in " ".join(command)
    assert "secret-test" in calls["input"]
    assert config.WORKER_REMOTE_TOKEN not in calls["input"]


def test_run_remote_action_rejects_mismatched_image_architecture_before_transfer(tmp_path, monkeypatch):
    """本机镜像与目标 Docker 架构不一致时，在传输大镜像前明确失败。"""
    _remote_config(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_workers.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(remote_workers, "_local_docker_image_platform", lambda _image: "linux/arm64")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="linux/amd64\n", stderr="")

    monkeypatch.setattr(remote_workers.subprocess, "run", fake_run)
    monkeypatch.setattr(
        remote_workers,
        "_stream_worker_image_to_remote",
        lambda *_args, **_kwargs: pytest.fail("架构不匹配时不应传输镜像"),
    )

    with pytest.raises(
        remote_workers.RemoteWorkerError,
        match="平台镜像架构 linux/arm64 与目标 Docker 架构 linux/amd64 不一致",
    ):
        remote_workers.run_remote_action(_server(), "install")

    assert len(calls) == 1
    assert calls[0][-1].startswith("docker info")


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
        remote_workers.subprocess,
        "run",
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
        calls["environment"] = kwargs["env"]
        askpass = Path(kwargs["env"]["SSH_ASKPASS"])
        calls["askpass_mode"] = stat.S_IMODE(askpass.stat().st_mode)
        prompt = real_subprocess_run(
            [str(askpass)], env=kwargs["env"], capture_output=True, text=True
        )
        calls["prompt"] = prompt.stdout.rstrip("\n")
        return subprocess.CompletedProcess(command, 0, stdout=f"connected {password}", stderr="")

    monkeypatch.setattr(remote_workers.subprocess, "run", fake_run)
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

    monkeypatch.setattr(remote_workers.subprocess, "run", fake_run)
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
        calls["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="worker running", stderr="")

    monkeypatch.setattr(remote_workers.subprocess, "run", fake_run)
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
        environment = kwargs["env"]
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

    monkeypatch.setattr(remote_workers.subprocess, "run", fake_run)
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
    _remote_config(tmp_path, monkeypatch)
    manager.lease.delete_remote_ssh_credentials()
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
        calls["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 255, stdout="", stderr=f"rejected {secret}")

    monkeypatch.setattr(remote_workers.subprocess, "run", fake_run)
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

    monkeypatch.setattr(remote_workers.subprocess, "run", fake_run)
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


def test_execute_remote_operation_persists_success_and_failure(monkeypatch, request):
    """远程执行结果更新 Redis/嵌入式状态，异常不写入密钥或堆栈信息。"""
    machine_id = "remote-operation-test"
    operation_id = "operation-test"
    server = _server(machine_id=machine_id)
    request.addfinalizer(lambda: manager.lease.delete_remote_server(machine_id))
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
        assert "image inspect registry.example.net/ml/worker:unit" in commands
        assert "pull registry.example.net/ml/worker:unit" not in commands
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
