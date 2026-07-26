import json
import tempfile
from pathlib import Path

import pytest

from core.services.extension_service import (
    ExtensionManifestError,
    ExtensionService,
)


def plugin_document(**updates):
    document = {
        "schema_version": 1,
        "id": "focus-games",
        "name": "专注小游戏",
        "version": "1.0.0",
        "description": "提供不执行代码的提示词玩法",
        "gameplay": [
            {
                "id": "focus-games.three-things",
                "name": "三件小事",
                "icon": "🎯",
                "prompt": "请带我完成三件两分钟内能做完的小事。",
                "enabled": True,
            }
        ],
    }
    document.update(updates)
    return document


def test_declarative_plugin_installs_and_contributes_gameplay():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source.json"
        source.write_text(
            json.dumps(plugin_document(), ensure_ascii=False),
            encoding="utf-8",
        )
        service = ExtensionService(root / "plugins")

        installed = service.install_plugin(source)
        gameplay = service.available_gameplay(
            [],
            [installed.id],
        )

        assert installed.path.parent == (root / "plugins").resolve()
        assert [item.name for item in gameplay] == ["三件小事"]
        assert gameplay[0].source == "focus-games"


def test_plugin_cannot_declare_executable_entrypoint():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "unsafe.json"
        path.write_text(
            json.dumps(
                plugin_document(entrypoint="plugin.py"),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with pytest.raises(
            ExtensionManifestError,
            match="可执行入口",
        ):
            ExtensionService(Path(directory) / "plugins").inspect_plugin(
                path
            )


def test_gameplay_validation_rejects_duplicate_ids():
    item = {
        "id": "same-id",
        "name": "玩法",
        "icon": "✨",
        "prompt": "开始玩法",
    }
    with pytest.raises(ExtensionManifestError, match="重复"):
        ExtensionService.normalize_gameplay([item, item])
