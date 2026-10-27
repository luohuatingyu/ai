"""远程服务器及 SSH 凭证 MySQL 仓储持久化和旧 Redis 导入测试。"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

import app.config as config
from app import database, remote_config_repository
from app.models import RemoteConfigMigration, RemoteServerConfig, RemoteSSHCredentialStore
from app.state import manager


def test_remote_server_and_credential_ciphertext_persist_in_database(
    remote_mysql_db, monkeypatch
):
    """服务器记录和已加密凭证可跨会话读取，服务器 Redis URL 只保存密文。"""
    monkeypatch.setattr(config, "USE_DATABASE", True)
    manager.lease.delete_remote_ssh_credentials()
    server = {
        "machine_id": "mysql-remote-server",
        "host": "compute.example.net",
        "username": "worker",
        "ssh_credential_id": "credential-one",
        "remote_redis_url_encrypted": "encrypted-redis-url",
    }
    remote_config_repository.save_remote_server(server)

    key = Fernet.generate_key()
    secret = "page-private-key-and-password"
    ciphertext = Fernet(key).encrypt(json.dumps({"secret": secret}).encode()).decode()
    remote_config_repository.save_ssh_credentials_ciphertext(ciphertext)

    # 通过新 SQLAlchemy 会话读取，验证数据来自关系数据库而非 LeaseStore。
    assert remote_config_repository.get_remote_server(server["machine_id"]) == server
    assert remote_config_repository.list_remote_servers() == [server]
    stored_ciphertext = remote_config_repository.get_ssh_credentials_ciphertext()
    assert stored_ciphertext == ciphertext
    assert secret not in stored_ciphertext

    session = database.SessionLocal()
    try:
        assert session.get(RemoteServerConfig, server["machine_id"]).config_data == server
        assert session.get(RemoteSSHCredentialStore, "platform").encrypted_credentials == ciphertext
    finally:
        session.close()

    remote_config_repository.delete_remote_server(server["machine_id"])
    remote_config_repository.delete_ssh_credentials()
    assert remote_config_repository.get_remote_server(server["machine_id"]) is None
    assert remote_config_repository.get_ssh_credentials_ciphertext() is None


def test_legacy_redis_remote_config_migrates_once_without_overwriting_mysql(
    remote_mysql_db, monkeypatch
):
    """旧 Redis 数据只导入一次，保留原值及已存在的 MySQL 服务器配置。"""
    monkeypatch.setattr(config, "USE_DATABASE", True)
    machine_id = "legacy-remote-server"
    legacy_server = {"machine_id": machine_id, "host": "legacy.example.net", "status": "online"}
    manager.lease.set_remote_server(machine_id, legacy_server)
    legacy_ciphertext = Fernet.generate_key().decode("ascii")
    manager.lease.set_remote_ssh_credentials(legacy_ciphertext)

    session = database.SessionLocal()
    try:
        session.add(
            RemoteServerConfig(
                machine_id=machine_id,
                config_data={"machine_id": machine_id, "host": "mysql.example.net"},
            )
        )
        session.commit()
    finally:
        session.close()

    remote_config_repository.migrate_remote_config_from_redis()

    assert remote_config_repository.get_remote_server(machine_id)["host"] == "mysql.example.net"
    assert remote_config_repository.get_ssh_credentials_ciphertext() == legacy_ciphertext
    assert manager.lease.get_remote_server(machine_id) == legacy_server
    assert manager.lease.get_remote_ssh_credentials() == legacy_ciphertext

    # 删除已迁入的凭证后再次运行迁移也不能复活旧 Redis 凭证。
    remote_config_repository.delete_ssh_credentials()
    remote_config_repository.migrate_remote_config_from_redis()
    assert remote_config_repository.get_ssh_credentials_ciphertext() is None


def test_remote_mysql_repository_rejects_disabled_database(monkeypatch):
    """USE_DATABASE=false 时远程配置不会回退写入 Redis 或进程内存。"""
    monkeypatch.setattr(config, "USE_DATABASE", False)

    with pytest.raises(remote_config_repository.RemoteConfigPersistenceError, match="启用 MySQL"):
        remote_config_repository.list_remote_servers()


def test_legacy_migration_marker_is_written_after_success(remote_mysql_db, monkeypatch):
    """无旧 Redis 数据时也写入迁移标记，后续删除数据不会被旧源重新导入。"""
    monkeypatch.setattr(config, "USE_DATABASE", True)
    manager.lease.delete_remote_ssh_credentials()

    remote_config_repository.migrate_remote_config_from_redis()

    session = database.SessionLocal()
    try:
        assert session.get(RemoteConfigMigration, "redis-remote-config-v1") is not None
    finally:
        session.close()
