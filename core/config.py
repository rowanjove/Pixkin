import json
import os
import shutil
import tempfile
import time
from typing import Dict, Any

from core.paths import user_data_dir
from core.storage.migrations import (
    CONFIG_SCHEMA_VERSION,
    StorageMigrationError,
    migrate_config,
)
from core.version import VERSION

DEFAULT_CONFIG: Dict[str, Any] = {
    "schema_version": CONFIG_SCHEMA_VERSION,
    "api": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-v4-flash",
        "preset": "balanced",
        "input_cost_per_million": 0.0,
        "output_cost_per_million": 0.0,
    },
    "image_generation": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-image-2",
        "quality": "medium"
    },
    "pet": {
        "scale": 1.0,
        "topmost": True,
        "edge_dock_enabled": True,
        "edge_sides": {
            "left": True,
            "right": True,
            "top": True,
            "bottom": False,
        },
        "edge_hover_enabled": True,
        "return_to_edge_after_alert": True,
        "dock_margin": 15,
        "peek_size": 45,
        "position": {},
        "system_prompt": "你是山山，Pixkin 桌面上的卡通 AI 伙伴。你温柔、聪慧、安静又细心，回答简洁、清楚、实用；需要时会调用已启用的安全工具，并明确说明执行结果。",
        "character": "default"
    },
    "character": {
        "active_pack": "shanshan"
    },
    "user": {
        "display_name": "我",
        "avatar_path": ""
    },
    "privacy": {
        "history_retention_days": -1,
        "model_notice_acknowledged": False,
        "model_notice_fingerprint": "",
        "context_permissions": {
            "window_metadata": "deny",
            "system_state": "deny",
            "clipboard": "deny",
            "screen": "deny",
            "plugin": "deny",
            "mcp": "deny",
            "microphone": "deny",
            "camera": "deny",
            "filesystem": "deny",
            "network": "deny",
            "external_action": "deny",
        },
    },
    "voice_input": {
        "enabled": False,
        "muted": False,
        "base_url": "https://api.openai.com/v1",
        "model": "whisper-1",
        "privacy_notice_acknowledged": False,
        "privacy_notice_fingerprint": "",
        "hotkeys_enabled": True,
        "hotkeys": {
            "toggle_pet": "Ctrl+Alt+P",
            "open_chat": "Ctrl+Alt+C",
            "stop_generation": "Ctrl+Alt+S",
            "mute_voice": "Ctrl+Alt+M",
        },
    },
    "voice_output": {
        "enabled": False,
        "provider": "windows_sapi",
        "voice": "",
        "speed": 1.0,
        "base_url": "https://api.openai.com/v1",
        "model": "tts-1",
    },
    "proactive": {
        # Desktop context observation is opt-in until the unified permission
        # service is enabled for each individual sensor.
        "enabled": False,
        "quiet_fullscreen": True,
        "work_stretch_reminder": True,
        "work_stretch_interval_minutes": 90,
        "sleep_guard": False,
        "sleep_guard_hour": 23,
        "sleep_guard_minute": 30,
        "min_prompt_interval_seconds": 3600,
    },
    "app": {
        "first_run_complete": False,
        "start_with_windows": False,
        "theme": "system",
        "version": VERSION,
        "automatic_updates": True,
        "update_channel": "stable",
        "last_update_check": "",
        "update_manifest_urls": {
            "stable": "",
            "beta": "",
        },
    },
    "extensions": {
        "gameplay": [
            {
                "id": "mood-fortune",
                "name": "抽一张今日心情签",
                "icon": "🌙",
                "prompt": (
                    "给我抽一张今天的心情签，写上签名、解读和一个小行动。"
                ),
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
                "prompt": (
                    "根据我们聊过的内容，用具体又不油腻的方式夸夸我。"
                ),
                "enabled": True,
            },
        ],
        "enabled_plugins": [],
    },
    "live_monitor": {
        "enabled": True,
        "notify_if_live_on_start": False,
        "interval_seconds": 60,
        "quiet_start": "22:00",
        "quiet_end": "08:00",
        "repeat_reminder_minutes": 0,
        "enabled_adapter_plugins": [],
        "rooms": []
    }
}

class ConfigManager:
    """配置文件管理器，负责配置的读取、更新与持久化保存"""

    def __init__(self, config_path: str | None = None):
        if config_path is None:
            target = user_data_dir() / "config.json"
            legacy = os.path.abspath("config.json")
            if not target.exists() and os.path.isfile(legacy):
                try:
                    shutil.copy2(legacy, target)
                except OSError:
                    pass
            self.config_path = str(target)
        else:
            self.config_path = os.path.abspath(config_path)
        self._config_data: Dict[str, Any] = {}
        self._write_blocked = False
        self.recovery_backup_path: str | None = None
        self.load_config()

    def load_config(self) -> Dict[str, Any]:
        """从本地文件读取配置，若不存在则创建默认配置"""
        if not os.path.exists(self.config_path):
            self._config_data = json.loads(json.dumps(DEFAULT_CONFIG))
            self.save_config()
            return self._config_data

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data, migrated = migrate_config(data)
            # 递归补全默认键值
            self._config_data = self._merge_defaults(data, DEFAULT_CONFIG)
            if migrated or self._config_data != data:
                self.save_config()
        except StorageMigrationError:
            raise
        except Exception as e:
            backup_path = (
                f"{self.config_path}.corrupt-{time.time_ns()}.bak"
            )
            try:
                shutil.copy2(self.config_path, backup_path)
                self.recovery_backup_path = backup_path
            except OSError as backup_error:
                self._write_blocked = True
                print(
                    "[ConfigManager] 无法备份损坏的配置文件，"
                    f"已禁止覆盖原文件: {backup_error}"
                )
            print(
                f"[ConfigManager] 读取配置文件失败: {e}，"
                "将恢复内存默认配置。"
            )
            self._config_data = json.loads(json.dumps(DEFAULT_CONFIG))

        return self._config_data

    @property
    def schema_version(self) -> int:
        return int(self._config_data["schema_version"])

    def save_config(self) -> bool:
        """以同目录临时文件原子保存配置，避免留下半写入 JSON。"""
        if self._write_blocked:
            print(
                "[ConfigManager] 配置写入已阻止："
                "损坏的原文件尚未成功备份。"
            )
            return False
        temporary_path = None
        try:
            directory = os.path.dirname(self.config_path) or "."
            os.makedirs(directory, exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=".pixkin-config-",
                suffix=".tmp",
                dir=directory,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as f:
                json.dump(self._config_data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, self.config_path)
            temporary_path = None
            return True
        except Exception as e:
            print(f"[ConfigManager] 保存配置文件失败: {e}")
            return False
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def get(self, section: str, key: Any = None, default: Any = None) -> Any:
        """获取指定配置项。若 key 为 None 或为默认字典，则返回整个 section"""
        if key is None or isinstance(key, (dict, list)):
            def_val = key if isinstance(key, (dict, list)) else default
            return self._config_data.get(section, def_val)
        section_data = self._config_data.get(section, {})
        if isinstance(section_data, dict):
            return section_data.get(key, default)
        return default

    def set(self, section: str, key: str, value: Any) -> bool:
        """更新配置项并保存"""
        return self.update_section(section, {key: value})

    def update_section(self, section: str, values: Dict[str, Any]) -> bool:
        """批量更新一个配置分区，只触发一次磁盘写入。"""
        return self.update_sections({section: values})

    def update_sections(self, updates: Dict[str, Dict[str, Any]]) -> bool:
        """以一次原子写入提交多个配置分区；失败时回滚内存状态。"""
        original = json.loads(json.dumps(self._config_data))
        for section, values in updates.items():
            section_data = self._config_data.setdefault(section, {})
            if not isinstance(section_data, dict):
                section_data = {}
                self._config_data[section] = section_data
            section_data.update(values)
        if self.save_config():
            return True
        self._config_data = original
        return False

    def _merge_defaults(self, user_data: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
        """补齐缺失的配置项"""
        res = json.loads(json.dumps(defaults))
        for key, val in user_data.items():
            if key in res and isinstance(res[key], dict) and isinstance(val, dict):
                res[key] = self._merge_defaults(val, res[key])
            else:
                res[key] = val
        return res
