import json
import os
import shutil
import tempfile
from typing import Dict, Any

from core.paths import user_data_dir
from core.version import VERSION

DEFAULT_CONFIG: Dict[str, Any] = {
    "api": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-v4-flash"
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
    "app": {
        "first_run_complete": False,
        "start_with_windows": False,
        "theme": "system",
        "version": VERSION
    },
    "live_monitor": {
        "enabled": True,
        "notify_if_live_on_start": False,
        "interval_seconds": 60,
        "rooms": []
    }
}

class ConfigManager:
    """配置文件管理器，负责配置的读取、更新与持久化保存"""

    def __init__(self, config_path: str = None):
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
            # 递归补全默认键值
            self._config_data = self._merge_defaults(data, DEFAULT_CONFIG)
            if self._migrate_legacy_values():
                self.save_config()
        except Exception as e:
            print(f"[ConfigManager] 读取配置文件失败: {e}，将恢复默认配置。")
            self._config_data = json.loads(json.dumps(DEFAULT_CONFIG))

        return self._config_data

    def _migrate_legacy_values(self) -> bool:
        api = self._config_data.get("api", {})
        if not isinstance(api, dict):
            return False
        base_url = str(api.get("base_url") or "").lower()
        model = str(api.get("model") or "")
        if (
            "api.deepseek.com" in base_url
            and model in {"deepseek-chat", "deepseek-reasoner"}
        ):
            api["model"] = "deepseek-v4-flash"
            return True
        return False

    def save_config(self) -> bool:
        """以同目录临时文件原子保存配置，避免留下半写入 JSON。"""
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
