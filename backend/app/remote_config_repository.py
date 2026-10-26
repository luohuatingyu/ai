"""远程服务器与 SSH 凭证的 MySQL 持久化仓储。"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import app.config as config
import app.database as database
from app.models import RemoteConfigMigration, RemoteServerConfig, RemoteSSHCredentialStore

_CREDENTIAL_STORE_ID = "platform"
_REDIS_MIGRATION_ID = "redis-remote-config-v1"


class RemoteConfigPersistenceError(RuntimeError):
    """远程服务器与 SSH 凭证无法安全持久化到 MySQL。"""


def _require_database() -> None:
    """拒绝将远程管理配置回退保存到 Redis 或进程内存。"""
    if not config.USE_DATABASE:
        raise RemoteConfigPersistenceError(
            "远程服务器和 SSH 凭证持久化需要启用 MySQL（USE_DATABASE=true）"
        )


def _mysql_error() -> RemoteConfigPersistenceError:
    """生成不包含连接凭据的数据库错误信息。"""
    return RemoteConfigPersistenceError("MySQL 远程配置存储不可用，请检查数据库连接和表结构")


def migrate_remote_config_from_redis() -> None:
    """幂等导入旧 Redis 服务器记录和凭证密文，保留已有 MySQL 数据。"""
    _require_database()
    session = None
    try:
        session = database.SessionLocal()
        if session.get(RemoteConfigMigration, _REDIS_MIGRATION_ID):
            return
        session.close()
        session = None

        # 延迟导入以避免数据库初始化阶段与 JobManager 形成循环依赖。
        from app.state import manager

        legacy_servers = manager.lease.list_remote_servers()
        legacy_credentials = manager.lease.get_remote_ssh_credentials()
        session = database.SessionLocal()
        if session.get(RemoteConfigMigration, _REDIS_MIGRATION_ID):
            return
        for server in legacy_servers:
            machine_id = str(server.get("machine_id", ""))
            if machine_id and session.get(RemoteServerConfig, machine_id) is None:
                session.add(RemoteServerConfig(machine_id=machine_id, config_data=dict(server)))
        if legacy_credentials and session.get(
            RemoteSSHCredentialStore, _CREDENTIAL_STORE_ID
        ) is None:
            session.add(
                RemoteSSHCredentialStore(
                    store_id=_CREDENTIAL_STORE_ID,
                    encrypted_credentials=str(legacy_credentials),
                )
            )
        session.add(RemoteConfigMigration(migration_id=_REDIS_MIGRATION_ID))
        session.commit()
    except IntegrityError:
        if session is not None:
            session.rollback()
        # 多个 trainer 同时启动时，另一个实例可能已经完成同一导入。
        check_session = None
        try:
            check_session = database.SessionLocal()
            if check_session.get(RemoteConfigMigration, _REDIS_MIGRATION_ID):
                return
        except SQLAlchemyError as exc:
            raise _mysql_error() from exc
        finally:
            if check_session is not None:
                check_session.close()
        raise _mysql_error()
    except RemoteConfigPersistenceError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RemoteConfigPersistenceError(
            "迁移旧 Redis 远程配置失败，请检查 Redis 与 MySQL 连接"
        ) from exc
    finally:
        if session is not None:
            session.close()


def _session() -> database.SessionLocal:
    """先确保旧配置迁移完成，再打开本次仓储操作的数据库会话。"""
    _require_database()
    migrate_remote_config_from_redis()
    try:
        return database.SessionLocal()
    except SQLAlchemyError as exc:
        raise _mysql_error() from exc


def save_remote_server(info: Dict[str, object]) -> None:
    """新增或更新完整远程服务器记录。"""
    machine_id = str(info.get("machine_id", ""))
    if not machine_id:
        raise ValueError("远程服务器记录缺少 machine_id")
    session = _session()
    try:
        record = session.get(RemoteServerConfig, machine_id)
        if record is None:
            record = RemoteServerConfig(machine_id=machine_id, config_data=dict(info))
            session.add(record)
        else:
            record.config_data = dict(info)
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise _mysql_error() from exc
    finally:
        session.close()


def get_remote_server(machine_id: str) -> Optional[Dict[str, object]]:
    """按服务器标识读取配置副本。"""
    session = _session()
    try:
        record = session.get(RemoteServerConfig, machine_id)
        return dict(record.config_data) if record is not None else None
    except SQLAlchemyError as exc:
        raise _mysql_error() from exc
    finally:
        session.close()


def list_remote_servers() -> List[Dict[str, object]]:
    """按服务器标识稳定排序并读取所有远程服务器配置。"""
    session = _session()
    try:
        records = session.query(RemoteServerConfig).order_by(RemoteServerConfig.machine_id).all()
        return [dict(record.config_data) for record in records]
    except SQLAlchemyError as exc:
        raise _mysql_error() from exc
    finally:
        session.close()


def delete_remote_server(machine_id: str) -> None:
    """按服务器标识删除一条配置记录。"""
    session = _session()
    try:
        record = session.get(RemoteServerConfig, machine_id)
        if record is not None:
            session.delete(record)
            session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise _mysql_error() from exc
    finally:
        session.close()


def get_ssh_credentials_ciphertext() -> Optional[str]:
    """读取页面 SSH 凭证目录的 Fernet 密文。"""
    session = _session()
    try:
        record = session.get(RemoteSSHCredentialStore, _CREDENTIAL_STORE_ID)
        return record.encrypted_credentials if record is not None else None
    except SQLAlchemyError as exc:
        raise _mysql_error() from exc
    finally:
        session.close()


def save_ssh_credentials_ciphertext(encrypted_credentials: str) -> None:
    """保存整份页面 SSH 凭证目录的密文，不写入任何凭证明文。"""
    session = _session()
    try:
        record = session.get(RemoteSSHCredentialStore, _CREDENTIAL_STORE_ID)
        if record is None:
            record = RemoteSSHCredentialStore(
                store_id=_CREDENTIAL_STORE_ID,
                encrypted_credentials=encrypted_credentials,
            )
            session.add(record)
        else:
            record.encrypted_credentials = encrypted_credentials
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise _mysql_error() from exc
    finally:
        session.close()


def delete_ssh_credentials() -> None:
    """删除 MySQL 中的平台 SSH 凭证密文记录。"""
    session = _session()
    try:
        record = session.get(RemoteSSHCredentialStore, _CREDENTIAL_STORE_ID)
        if record is not None:
            session.delete(record)
            session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise _mysql_error() from exc
    finally:
        session.close()
