"""Strict manifests for process-isolated Pixkin plugins."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from core.runtime.permissions import PermissionOperation, PermissionResource


PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
PLUGIN_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
MAX_MANIFEST_BYTES = 128 * 1024


class PluginManifestError(ValueError):
    pass


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    api_version: int
    command: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    permissions: Mapping[str, Any] = field(default_factory=dict)
    path: Path | None = None
    isolation: str = "process"

    @classmethod
    def load(cls, source: str | Path) -> "PluginManifest":
        path = Path(source).resolve()
        try:
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                raise PluginManifestError("插件清单超过 128 KiB")
            document = json.loads(path.read_text(encoding="utf-8"))
        except PluginManifestError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginManifestError("插件清单无法读取") from exc
        return cls.from_dict(document, path=path)

    @classmethod
    def from_dict(
        cls,
        document: Mapping[str, Any],
        *,
        path: Path | None = None,
    ) -> "PluginManifest":
        if not isinstance(document, Mapping):
            raise PluginManifestError("插件清单必须是对象")
        allowed = {
            "schema_version", "id", "name", "version", "api_version",
            "command", "capabilities", "permissions", "isolation",
        }
        unknown = set(document) - allowed
        if unknown:
            raise PluginManifestError(
                f"插件清单包含不支持的字段：{', '.join(sorted(unknown))}"
            )
        if document.get("schema_version") != 1:
            raise PluginManifestError("插件清单版本不兼容")
        plugin_id = str(document.get("id") or "").strip().lower()
        name = str(document.get("name") or "").strip()
        version = str(document.get("version") or "").strip()
        if not PLUGIN_ID.fullmatch(plugin_id):
            raise PluginManifestError("插件 ID 无效")
        if not name or len(name) > 80:
            raise PluginManifestError("插件名称无效")
        if not PLUGIN_VERSION.fullmatch(version):
            raise PluginManifestError("插件版本必须是 SemVer")
        try:
            api_version = int(document.get("api_version", 1))
        except (TypeError, ValueError) as exc:
            raise PluginManifestError("插件 API 版本无效") from exc
        if api_version != 1:
            raise PluginManifestError("插件 API 版本不受支持")
        raw_command = document.get("command")
        if (
            not isinstance(raw_command, list)
            or not raw_command
            or any(not isinstance(item, str) or not item.strip() for item in raw_command)
        ):
            raise PluginManifestError("插件必须提供非空 command 数组")
        raw_capabilities = document.get("capabilities", [])
        if not isinstance(raw_capabilities, list) or any(
            not isinstance(item, str) or not item.strip()
            for item in raw_capabilities
        ):
            raise PluginManifestError("插件 capabilities 必须是字符串数组")
        permissions = document.get("permissions", {})
        if not isinstance(permissions, Mapping):
            raise PluginManifestError("插件 permissions 必须是对象")
        normalized_permissions: dict[str, tuple[str, ...]] = {}
        known_resources = {item.value for item in PermissionResource}
        known_operations = {item.value for item in PermissionOperation}
        for raw_resource, raw_operations in permissions.items():
            resource = str(raw_resource or "").strip().lower()
            if resource not in known_resources:
                raise PluginManifestError(f"插件权限资源不受支持：{resource}")
            if isinstance(raw_operations, str):
                operations = (raw_operations.strip().lower(),)
            elif isinstance(raw_operations, (list, tuple, set, frozenset)):
                operations = tuple(
                    str(item or "").strip().lower()
                    for item in raw_operations
                )
            else:
                raise PluginManifestError(
                    f"插件权限操作必须是字符串或字符串数组：{resource}"
                )
            if not operations or any(item not in known_operations for item in operations):
                raise PluginManifestError(f"插件权限操作不受支持：{resource}")
            normalized_permissions[resource] = tuple(dict.fromkeys(operations))
        isolation = str(document.get("isolation", "process")).strip().lower()
        if isolation != "process":
            raise PluginManifestError("第三方插件必须使用 process 隔离")
        return cls(
            id=plugin_id,
            name=name,
            version=version,
            api_version=api_version,
            command=tuple(item.strip() for item in raw_command),
            capabilities=tuple(str(item).strip() for item in raw_capabilities),
            permissions=normalized_permissions,
            path=path,
            isolation=isolation,
        )
