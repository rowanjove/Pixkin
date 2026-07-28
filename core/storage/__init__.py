"""Persistent storage schemas and migrations."""

from core.storage.migrations import (
    CHAT_SCHEMA_VERSION,
    CONFIG_SCHEMA_VERSION,
    FutureSchemaVersionError,
    StorageMigrationError,
    migrate_chat_database,
    migrate_config,
)

__all__ = [
    "CHAT_SCHEMA_VERSION",
    "CONFIG_SCHEMA_VERSION",
    "FutureSchemaVersionError",
    "StorageMigrationError",
    "migrate_chat_database",
    "migrate_config",
]
