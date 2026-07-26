"""Safe prompt-based gameplay and plugin extension contracts."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


EXTENSION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
MAX_MANIFEST_BYTES = 128 * 1024
MAX_GAMEPLAY_ITEMS = 50
MAX_PROMPT_CHARS = 2000


DEFAULT_GAMEPLAY_EXTENSIONS = (
    {
        "id": "mood-fortune",
        "name": "抽一张今日心情签",
        "icon": "🌙",
        "prompt": "给我抽一张今天的心情签，写上签名、解读和一个小行动。",
        "enabled": True,
    },
    {
        "id": "reverse-questions",
        "name": "开启反向提问局",
        "icon": "🧠",
        "prompt": "接下来由你问我三个有趣的问题，一次只问一个。",
        "enabled": True,
    },
    {
        "id": "inspiration-chain",
        "name": "玩灵感接龙",
        "icon": "🎨",
        "prompt": "和我玩灵感接龙：你先给出一个画面，我接着补充。",
        "enabled": True,
    },
    {
        "id": "specific-praise",
        "name": "把我夸到起飞",
        "icon": "🚀",
        "prompt": "根据我们聊过的内容，用具体又不油腻的方式夸夸我。",
        "enabled": True,
    },
)


class ExtensionManifestError(ValueError):
    pass


@dataclass(frozen=True)
class GameplayExtension:
    id: str
    name: str
    icon: str
    prompt: str
    enabled: bool = True
    source: str = "local"

    def to_config(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("source", None)
        return value


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    description: str
    gameplay: tuple[GameplayExtension, ...]
    path: Path


class ExtensionService:
    """Manage declarative extensions without loading executable code."""

    SCHEMA_VERSION = 1

    def __init__(self, plugin_root: str | Path):
        self.plugin_root = Path(plugin_root)

    @classmethod
    def normalize_gameplay(
        cls,
        values: Any,
        *,
        source: str = "local",
    ) -> list[GameplayExtension]:
        if values is None:
            values = DEFAULT_GAMEPLAY_EXTENSIONS
        if not isinstance(values, Iterable) or isinstance(
            values, (str, bytes, dict)
        ):
            raise ExtensionManifestError("玩法扩展列表无效")
        result: list[GameplayExtension] = []
        seen: set[str] = set()
        for raw in values:
            if not isinstance(raw, dict):
                raise ExtensionManifestError("玩法扩展条目无效")
            extension_id = str(raw.get("id") or "").strip().lower()
            name = str(raw.get("name") or "").strip()
            icon = str(raw.get("icon") or "✨").strip()[:4] or "✨"
            prompt = str(raw.get("prompt") or "").strip()
            if (
                not EXTENSION_ID_PATTERN.fullmatch(extension_id)
                or extension_id in seen
            ):
                raise ExtensionManifestError(
                    f"玩法扩展 ID 无效或重复：{extension_id}"
                )
            if not name or len(name) > 40:
                raise ExtensionManifestError("玩法名称不能为空或超过 40 字")
            if not prompt or len(prompt) > MAX_PROMPT_CHARS:
                raise ExtensionManifestError(
                    "玩法提示词不能为空或超过 2000 字"
                )
            seen.add(extension_id)
            result.append(
                GameplayExtension(
                    id=extension_id,
                    name=name,
                    icon=icon,
                    prompt=prompt,
                    enabled=bool(raw.get("enabled", True)),
                    source=source,
                )
            )
            if len(result) > MAX_GAMEPLAY_ITEMS:
                raise ExtensionManifestError("玩法扩展不能超过 50 项")
        return result

    def list_plugins(self) -> list[PluginManifest]:
        if not self.plugin_root.is_dir():
            return []
        plugins = []
        for path in sorted(self.plugin_root.glob("*.json")):
            try:
                plugins.append(self.inspect_plugin(path))
            except ExtensionManifestError:
                continue
        return plugins

    def inspect_plugin(self, source: str | Path) -> PluginManifest:
        path = Path(source)
        try:
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                raise ExtensionManifestError("插件清单超过 128 KiB")
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ExtensionManifestError("插件清单不存在") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExtensionManifestError("插件清单无法读取") from exc
        if not isinstance(document, dict):
            raise ExtensionManifestError("插件清单根节点必须是对象")
        allowed_keys = {
            "schema_version",
            "id",
            "name",
            "version",
            "description",
            "gameplay",
        }
        if set(document) - allowed_keys:
            raise ExtensionManifestError(
                "插件清单包含不支持的字段；插件不能声明可执行入口"
            )
        if document.get("schema_version") != self.SCHEMA_VERSION:
            raise ExtensionManifestError("插件清单版本不兼容")
        plugin_id = str(document.get("id") or "").strip().lower()
        name = str(document.get("name") or "").strip()
        version = str(document.get("version") or "").strip()
        description = str(document.get("description") or "").strip()
        if not EXTENSION_ID_PATTERN.fullmatch(plugin_id):
            raise ExtensionManifestError("插件 ID 无效")
        if not name or len(name) > 60:
            raise ExtensionManifestError("插件名称无效")
        if not version or len(version) > 32:
            raise ExtensionManifestError("插件版本无效")
        gameplay = tuple(
            self.normalize_gameplay(
                document.get("gameplay", []),
                source=plugin_id,
            )
        )
        if not gameplay:
            raise ExtensionManifestError("插件至少需要提供一项玩法")
        return PluginManifest(
            id=plugin_id,
            name=name,
            version=version,
            description=description[:300],
            gameplay=gameplay,
            path=path.resolve(),
        )

    def install_plugin(self, source: str | Path) -> PluginManifest:
        inspected = self.inspect_plugin(source)
        self.plugin_root.mkdir(parents=True, exist_ok=True)
        destination = self.plugin_root / f"{inspected.id}.json"
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{inspected.id}.",
            suffix=".tmp",
            dir=self.plugin_root,
        )
        os.close(descriptor)
        temporary_path = Path(temporary)
        try:
            shutil.copyfile(inspected.path, temporary_path)
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)
        return self.inspect_plugin(destination)

    def remove_plugin(self, plugin_id: str) -> bool:
        value = str(plugin_id).strip().lower()
        if not EXTENSION_ID_PATTERN.fullmatch(value):
            raise ExtensionManifestError("插件 ID 无效")
        path = self.plugin_root / f"{value}.json"
        if not path.is_file():
            return False
        path.unlink()
        return True

    def available_gameplay(
        self,
        configured: Any,
        enabled_plugins: Any,
    ) -> list[GameplayExtension]:
        local = self.normalize_gameplay(configured)
        enabled = {
            str(value)
            for value in (
                enabled_plugins
                if isinstance(enabled_plugins, list)
                else []
            )
        }
        result = [item for item in local if item.enabled]
        for plugin in self.list_plugins():
            if plugin.id not in enabled:
                continue
            result.extend(item for item in plugin.gameplay if item.enabled)
        return result
