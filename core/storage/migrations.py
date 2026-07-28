"""Versioned, repeatable migrations for Pixkin local storage."""

from __future__ import annotations

import copy
import sqlite3
from typing import Any, Callable


CONFIG_SCHEMA_VERSION = 1
CHAT_SCHEMA_VERSION = 1


class StorageMigrationError(RuntimeError):
    """A local storage schema could not be migrated safely."""


class FutureSchemaVersionError(StorageMigrationError):
    """Local data was written by a newer Pixkin schema."""


def _config_0_to_1(data: dict[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(data)
    api = migrated.get("api")
    if isinstance(api, dict):
        base_url = str(api.get("base_url") or "").lower()
        model = str(api.get("model") or "")
        if (
            "api.deepseek.com" in base_url
            and model in {"deepseek-chat", "deepseek-reasoner"}
        ):
            api["model"] = "deepseek-v4-flash"
    migrated["schema_version"] = 1
    return migrated


CONFIG_MIGRATIONS: dict[
    int,
    Callable[[dict[str, Any]], dict[str, Any]],
] = {
    0: _config_0_to_1,
}


def migrate_config(
    data: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Return config migrated to the current schema without mutating input."""
    if not isinstance(data, dict):
        raise StorageMigrationError("配置文件根节点必须是 JSON 对象。")
    raw_version = data.get("schema_version", 0)
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise StorageMigrationError("配置 schema_version 必须是整数。")
    if raw_version < 0:
        raise StorageMigrationError("配置 schema_version 不能小于 0。")
    if raw_version > CONFIG_SCHEMA_VERSION:
        raise FutureSchemaVersionError(
            f"配置版本 {raw_version} 高于当前支持的 "
            f"{CONFIG_SCHEMA_VERSION}，已拒绝覆盖。"
        )
    migrated = copy.deepcopy(data)
    version: int = int(raw_version)
    while version < CONFIG_SCHEMA_VERSION:
        migration = CONFIG_MIGRATIONS.get(version)
        if migration is None:
            raise StorageMigrationError(
                f"缺少配置版本 {version} 的迁移步骤。"
            )
        migrated = migration(migrated)
        next_version = migrated.get("schema_version")
        if (
            isinstance(next_version, bool)
            or not isinstance(next_version, int)
            or next_version != version + 1
        ):
            raise StorageMigrationError(
                f"配置迁移 {version} 未生成预期版本 {version + 1}。"
            )
        version = int(next_version)
    return migrated, migrated != data


def _chat_0_to_1(connection: sqlite3.Connection):
    statements = (
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            character_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_session_per_character
        ON sessions(character_id)
        WHERE is_active = 1
        """,
        """
        CREATE INDEX IF NOT EXISTS sessions_by_character
        ON sessions(character_id, updated_at DESC)
        """,
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS messages_by_session
        ON messages(session_id, id)
        """,
        """
        CREATE INDEX IF NOT EXISTS messages_by_date
        ON messages(created_at)
        """,
    )
    for statement in statements:
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 1")


CHAT_MIGRATIONS: dict[
    int,
    Callable[[sqlite3.Connection], None],
] = {
    0: _chat_0_to_1,
}


def migrate_chat_database(connection: sqlite3.Connection) -> int:
    """Migrate an open chat database inside the caller's transaction."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version < 0:
        raise StorageMigrationError(
            "聊天数据库 user_version 不能小于 0。"
        )
    if version > CHAT_SCHEMA_VERSION:
        raise FutureSchemaVersionError(
            f"聊天数据库版本 {version} 高于当前支持的 "
            f"{CHAT_SCHEMA_VERSION}，已拒绝写入。"
        )
    while version < CHAT_SCHEMA_VERSION:
        migration = CHAT_MIGRATIONS.get(version)
        if migration is None:
            raise StorageMigrationError(
                f"缺少聊天数据库版本 {version} 的迁移步骤。"
            )
        migration(connection)
        next_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if next_version != version + 1:
            raise StorageMigrationError(
                f"聊天数据库迁移 {version} 未生成预期版本 "
                f"{version + 1}。"
            )
        version = next_version
    return version
