import io
import hashlib
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

import yaml
from PIL import Image, UnidentifiedImageError

from core.config import ConfigManager
from core.paths import characters_dir


LEGACY_SUPPORTED_STATES = {
    "idle", "blink", "stretch", "wave", "nod", "sleep",
    "dragging", "edge_docked", "talking", "alerting",
}
STANDARD_STATES = {
    "idle", "talking", "dragging", "alerting",
    "blink", "look_around", "stretch", "wave", "nod", "sleep", "wake",
    "listening", "thinking", "working", "waiting", "success", "failed",
    "touch", "happy",
}
FULL_STATES = STANDARD_STATES | {
    "annoyed", "walk_left", "walk_right", "run_left", "run_right",
    "jump", "land", "alerting_important", "celebrate_live",
}
EDGE_STATES = {
    f"edge_{phase}_{side}"
    for phase in ("enter", "idle", "hover", "exit")
    for side in ("left", "right", "top", "bottom")
}
STATE_ALIASES = {
    "waving": "wave",
    "jumping": "jump",
    "review": "working",
    "running-left": "run_left",
    "running-right": "run_right",
}
SUPPORTED_STATES = (
    LEGACY_SUPPORTED_STATES
    | FULL_STATES
    | EDGE_STATES
    | set(STATE_ALIASES)
    | {"running"}
)
QUALITY_TIER_REQUIRED = {
    "basic": {"idle", "talking", "dragging", "alerting"},
    "standard": STANDARD_STATES,
    "full": FULL_STATES | EDGE_STATES,
}
V2_TOP_LEVEL_FIELDS = {
    "schema_version", "id", "name", "version", "author", "description",
    "quality_tier", "preview", "persona", "behavior", "animations",
    "compatibility", "rights", "edge", "extensions", "system_prompt",
}
V2_REQUIRED_FIELDS = {
    "schema_version", "id", "name", "version", "author", "description",
    "quality_tier", "persona", "behavior", "animations", "compatibility",
    "rights",
}
SUPPORTED_IMAGE_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg"}
MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_YAML_ALIASES = 200
MAX_YAML_DEPTH = 32
MAX_YAML_NODES = 20_000
MAX_FILE_COUNT = 500
MAX_IMAGE_PIXELS = 20_000_000
MAX_TOTAL_IMAGE_PIXELS = 32_000_000
MAX_FRAMES_PER_ANIMATION = 30
MAX_TOTAL_ANIMATION_FRAMES = 600
DEFAULT_PACKAGE_ID = "shanshan"
BUILTIN_PACKAGE_IDS = frozenset({"shanshan", "linlin", "pip"})
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
WINDOWS_FORBIDDEN_CHARS = frozenset('<>:"|?*')


class CharacterPackageError(ValueError):
    pass


@dataclass(frozen=True)
class FrameSpec:
    file: Path
    rect: Optional[Tuple[int, int, int, int]] = None


@dataclass(frozen=True)
class AnimationSpec:
    frames: List[FrameSpec]
    fps: int = 8
    playback: str = "loop"
    anchor: Tuple[int, int] = (96, 194)
    priority: int = 10
    interruptible: bool = True
    legacy_effects: bool = False

    @property
    def files(self) -> List[Path]:
        """v1 API compatibility for callers that only understand files."""
        return [frame.file for frame in self.frames]

    @property
    def loop(self) -> bool:
        return self.playback in {"loop", "ping_pong"}


@dataclass(frozen=True)
class CharacterPackage:
    package_id: str
    name: str
    version: str
    author: str
    description: str
    system_prompt: str
    root: Path
    preview: Optional[Path]
    animations: Dict[str, AnimationSpec]
    schema_version: str = "1.0"
    quality_tier: str = "legacy"
    persona: Dict[str, Any] = field(default_factory=dict)
    behavior: Dict[str, Any] = field(default_factory=dict)
    edge: Dict[str, Any] = field(default_factory=dict)
    compatibility: Dict[str, Any] = field(default_factory=dict)
    rights: Dict[str, Any] = field(default_factory=dict)

    def animation_for(self, state: str) -> Optional[AnimationSpec]:
        normalized = STATE_ALIASES.get(state, state)
        return self.animations.get(normalized) or self.animations.get("idle")

    @property
    def is_legacy(self) -> bool:
        try:
            return int(self.schema_version.split(".", 1)[0]) < 2
        except (TypeError, ValueError):
            return True


class _CharacterPackageTransactions:
    """Own mutable install, rename, delete, and archive comparison operations."""

    def __init__(self, manager):
        self.manager = manager

    def import_zip(
        self,
        zip_path: str,
        replace: bool = False,
        activate: bool = True,
        allow_builtin_replace: bool = False,
    ) -> CharacterPackage:
        manager = self.manager
        path = Path(zip_path)
        metadata, prefix = manager._read_zip_metadata(path)
        package_id = manager._validated_id(metadata.get("id", ""))
        target = manager.root / package_id
        if target.exists() and not replace:
            raise CharacterPackageError(
                f"角色包“{package_id}”已经安装，请先更换包 ID 或选择覆盖导入。"
            )
        if (
            target.exists()
            and replace
            and package_id in BUILTIN_PACKAGE_IDS
            and not allow_builtin_replace
        ):
            raise CharacterPackageError("内置角色不能被第三方角色包覆盖。")

        temp_dir = Path(
            tempfile.mkdtemp(prefix=".import-", dir=manager.root)
        )
        staging = temp_dir / package_id
        staging.mkdir()
        backup = None
        target_replaced = False
        try:
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    relative = manager._relative_member(
                        info.filename,
                        prefix,
                    )
                    if relative is None or info.is_dir():
                        continue
                    destination = staging / Path(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with (
                        archive.open(info) as source,
                        destination.open("wb") as output,
                    ):
                        shutil.copyfileobj(source, output)

            package = manager._load_from_directory(staging)
            if target.exists():
                backup = manager.root / f".backup-{package_id}"
                if backup.exists():
                    shutil.rmtree(backup)
                target.replace(backup)
            staging.replace(target)
            target_replaced = True
            package = manager._load_from_directory(target)
            if activate:
                manager.activate(package.package_id)
            if backup and backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            return package
        except Exception:
            if target_replaced and target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if backup and backup.exists():
                backup.replace(target)
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def rename_package(
        self,
        package_id: str,
        new_name: str,
    ) -> CharacterPackage:
        manager = self.manager
        package_id = manager._validated_id(package_id)
        if package_id in BUILTIN_PACKAGE_IDS:
            raise CharacterPackageError("内置角色不可重命名。")
        name = str(new_name or "").strip()
        if not name:
            raise CharacterPackageError("角色名字不能为空。")
        if len(name) > 32:
            raise CharacterPackageError("角色名字不能超过 32 个字符。")
        package = manager._load_from_directory(manager.root / package_id)
        md_path = package.root / "character.md"
        text = md_path.read_text(encoding="utf-8-sig")
        match = re.match(
            r"^\s*---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)",
            text,
            re.S,
        )
        metadata = manager._parse_frontmatter(text)
        old_name = str(metadata.get("name", ""))
        metadata["name"] = name
        if manager._schema_major(metadata) < 2:
            prompt = str(metadata.get("system_prompt", ""))
            if prompt and old_name:
                metadata["system_prompt"] = prompt.replace(old_name, name)
        body = text[match.end():] if match else ""
        frontmatter = yaml.safe_dump(
            metadata,
            allow_unicode=True,
            sort_keys=False,
        ).rstrip()
        updated = f"---\n{frontmatter}\n---\n\n{body.lstrip()}"
        names = {
            file.relative_to(package.root).as_posix()
            for file in package.root.rglob("*")
            if file.is_file()
        }
        manager._validate_metadata(metadata, names)
        temporary = md_path.with_suffix(".md.tmp")
        temporary.write_text(updated, encoding="utf-8")
        temporary.replace(md_path)
        renamed = manager._load_from_directory(package.root)
        if manager.config.get("character", "active_pack", "") == package_id:
            if not manager.config.update_section(
                "pet",
                {"system_prompt": renamed.system_prompt},
            ):
                temporary.write_text(text, encoding="utf-8")
                temporary.replace(md_path)
                raise CharacterPackageError(
                    "角色名称未保存：配置文件无法写入。"
                )
        return renamed

    def delete_package(
        self,
        package_id: str,
    ) -> Optional[CharacterPackage]:
        manager = self.manager
        package_id = manager._validated_id(package_id)
        if package_id in BUILTIN_PACKAGE_IDS:
            raise CharacterPackageError("内置角色不可删除。")
        target = manager.root / package_id
        package = manager._load_from_directory(target)
        active_id = manager.config.get("character", "active_pack", "")
        fallback = None
        if active_id == package.package_id:
            default_dir = manager.root / DEFAULT_PACKAGE_ID
            if not default_dir.is_dir():
                raise CharacterPackageError(
                    "缺少默认角色，无法删除当前角色。请先重新安装默认角色。"
                )
            fallback = manager.activate(DEFAULT_PACKAGE_ID)
        shutil.rmtree(target)
        return fallback

    def package_matches_zip(self, package_id: str, zip_path: str) -> bool:
        manager = self.manager
        target = manager.root / manager._validated_id(package_id)
        if not target.is_dir():
            return False
        path = Path(zip_path)
        try:
            metadata, prefix = manager._read_zip_metadata(path)
            if manager._validated_id(metadata.get("id", "")) != package_id:
                return False
            with zipfile.ZipFile(path) as archive:
                members = {
                    relative.as_posix(): info
                    for info in archive.infolist()
                    if (
                        (
                            relative := manager._relative_member(
                                info.filename,
                                prefix,
                            )
                        )
                        is not None
                        and not info.is_dir()
                    )
                }
                installed = {
                    file.relative_to(target).as_posix(): file
                    for file in target.rglob("*")
                    if file.is_file()
                }
                if set(members) != set(installed):
                    return False
                for name, info in members.items():
                    local = installed[name]
                    if local.stat().st_size != info.file_size:
                        return False
                    with (
                        archive.open(info) as source,
                        local.open("rb") as disk,
                    ):
                        if self.stream_digest(source) != self.stream_digest(
                            disk
                        ):
                            return False
            return True
        except (OSError, zipfile.BadZipFile, CharacterPackageError):
            return False

    @staticmethod
    def stream_digest(stream) -> bytes:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.digest()


class CharacterPackageManager:
    """安全导入、校验和激活 ZIP 角色包。"""

    def __init__(
        self,
        config_manager: ConfigManager,
        root: Optional[Path] = None,
    ):
        self.config = config_manager
        self.root = Path(root) if root else characters_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._transactions = _CharacterPackageTransactions(self)

    def list_packages(self) -> List[CharacterPackage]:
        packages = []
        for md_path in sorted(self.root.glob("*/character.md")):
            try:
                packages.append(self._load_from_directory(md_path.parent))
            except CharacterPackageError:
                continue
        return packages

    def get_active(self) -> Optional[CharacterPackage]:
        package_id = str(
            self.config.get("character", "active_pack", "")
        )
        if not package_id:
            return None
        try:
            package_id = self._validated_id(package_id)
        except CharacterPackageError:
            return None
        directory = self.root / package_id
        if not directory.is_dir():
            return None
        try:
            return self._load_from_directory(directory)
        except CharacterPackageError:
            return None

    def activate(self, package_id: str) -> CharacterPackage:
        package_id = self._validated_id(package_id)
        package = self._load_from_directory(self.root / package_id)
        updates = {
            "character": {"active_pack": package.package_id},
        }
        if package.system_prompt:
            updates["pet"] = {"system_prompt": package.system_prompt}
        if not self.config.update_sections(updates):
            raise CharacterPackageError("角色已加载，但配置文件无法保存。")
        return package

    def inspect_zip(self, zip_path: str) -> CharacterPackage:
        """只校验包，不落盘；root 使用 ZIP 路径占位。"""
        path = Path(zip_path)
        metadata, prefix = self._read_zip_metadata(path)
        return self._metadata_to_package(metadata, path, prefix=prefix)

    def read_zip_preview(self, zip_path: str) -> Optional[bytes]:
        """返回已通过角色包校验的预览图数据，供导入确认界面使用。"""
        path = Path(zip_path)
        metadata, prefix = self._read_zip_metadata(path)
        preview = str(metadata.get("preview") or "")
        if not preview:
            return None
        member = prefix + self._safe_relative(preview).as_posix()
        with zipfile.ZipFile(path) as archive:
            return archive.read(member)

    def import_zip(
        self,
        zip_path: str,
        replace: bool = False,
        activate: bool = True,
        allow_builtin_replace: bool = False,
    ) -> CharacterPackage:
        return self._transactions.import_zip(
            zip_path,
            replace=replace,
            activate=activate,
            allow_builtin_replace=allow_builtin_replace,
        )

    def rename_package(self, package_id: str, new_name: str) -> CharacterPackage:
        """只修改角色显示名，包 ID 和资源目录保持稳定。"""
        return self._transactions.rename_package(package_id, new_name)

    def delete_package(self, package_id: str) -> Optional[CharacterPackage]:
        """删除自定义角色；删除当前角色前自动切回默认角色。"""
        return self._transactions.delete_package(package_id)

    def package_matches_zip(self, package_id: str, zip_path: str) -> bool:
        """比较已安装目录与可信发行归档，用于内置角色升级和自修复。"""
        return self._transactions.package_matches_zip(package_id, zip_path)

    def _read_zip_metadata(self, path: Path):
        if path.suffix.lower() != ".zip" or not path.is_file():
            raise CharacterPackageError("请选择有效的 ZIP 角色包。")
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_FILE_COUNT:
                    raise CharacterPackageError("角色包文件数量过多。")
                total = 0
                md_candidates = []
                normalized_names = set()
                for info in infos:
                    self._validate_zip_member(info)
                    normalized_name = (
                        info.filename.replace("\\", "/")
                        .rstrip("/")
                        .casefold()
                    )
                    if normalized_name in normalized_names:
                        raise CharacterPackageError(
                            "角色包包含重复或大小写冲突的路径："
                            f"{info.filename}"
                        )
                    normalized_names.add(normalized_name)
                    total += info.file_size
                    if total > MAX_PACKAGE_BYTES:
                        raise CharacterPackageError("角色包解压后不能超过 100 MB。")
                    if PurePosixPath(info.filename.replace("\\", "/")).name == "character.md":
                        md_candidates.append(info)
                if len(md_candidates) != 1:
                    raise CharacterPackageError(
                        "ZIP 中必须且只能包含一个 character.md。"
                    )
                md_info = md_candidates[0]
                if md_info.file_size > MAX_METADATA_BYTES:
                    raise CharacterPackageError(
                        "character.md 不能超过 1 MB。"
                    )
                prefix = str(PurePosixPath(md_info.filename.replace("\\", "/")).parent)
                prefix = "" if prefix == "." else prefix.rstrip("/") + "/"
                text = archive.read(md_info).decode("utf-8-sig")
        except zipfile.BadZipFile as exc:
            raise CharacterPackageError("ZIP 文件已损坏或格式不正确。") from exc

        metadata = self._parse_frontmatter(text)
        # 用 ZIP 内文件表验证所有声明资源。
        names = set()
        for info in infos:
            relative = self._relative_member(info.filename, prefix)
            if relative is not None:
                names.add(relative.as_posix())
        self._validate_metadata(metadata, names)
        self._validate_zip_images(path=path, metadata=metadata, prefix=prefix)
        return metadata, prefix

    def _load_from_directory(self, directory: Path) -> CharacterPackage:
        md_path = directory / "character.md"
        if not md_path.is_file():
            raise CharacterPackageError("角色目录缺少 character.md。")
        metadata = self._parse_frontmatter(md_path.read_text(encoding="utf-8-sig"))
        names = {
            file.relative_to(directory).as_posix()
            for file in directory.rglob("*")
            if file.is_file()
        }
        self._validate_metadata(metadata, names)
        self._validate_directory_images(directory, metadata)
        return self._metadata_to_package(metadata, directory)

    def _metadata_to_package(
        self,
        metadata: Dict[str, Any],
        root: Path,
        prefix: str = "",
    ):
        schema_version = str(metadata.get("schema_version", "1.0"))
        schema_major = self._schema_major(metadata)
        animations: Dict[str, AnimationSpec] = {}
        raw_animations = metadata.get("animations", {})
        if not isinstance(raw_animations, dict):
            raise CharacterPackageError("animations 必须是对象。")
        for state_value, raw in raw_animations.items():
            state = str(state_value)
            normalized_state = STATE_ALIASES.get(state, state)
            if normalized_state in animations:
                raise CharacterPackageError(
                    f"动作别名与标准动作重复定义：{state}"
                )
            if isinstance(raw, str):
                raw = {"files": [raw]}
            if not isinstance(raw, dict):
                raise CharacterPackageError(
                    f"动作 {state} 的配置必须是对象或图片路径。"
                )
            frames = self._frames_from_animation(
                raw, root=root, prefix=prefix, schema_major=schema_major
            )
            playback = (
                str(raw.get("playback", "loop"))
                if schema_major >= 2
                else ("loop" if bool(raw.get("loop", True)) else "once")
            )
            anchor = self._int_pair(raw.get("anchor", [96, 194]), "anchor")
            animations[normalized_state] = AnimationSpec(
                frames=frames,
                fps=max(
                    1,
                    min(30, self._int_value(raw.get("fps", 8), "fps")),
                ),
                playback=playback,
                anchor=anchor,
                priority=max(
                    0,
                    min(
                        100,
                        self._int_value(
                            raw.get(
                                "priority",
                                self._default_priority(normalized_state),
                            ),
                            "priority",
                        ),
                    ),
                ),
                interruptible=bool(raw.get("interruptible", True)),
                legacy_effects=schema_major < 2 and len(frames) == 1,
            )
        preview_raw = metadata.get("preview", "")
        preview = (
            root / Path(*PurePosixPath(prefix + preview_raw).parts)
            if preview_raw else None
        )
        persona = dict(metadata.get("persona") or {})
        behavior = dict(metadata.get("behavior") or {})
        system_prompt = str(metadata.get("system_prompt", "")).strip()
        if schema_major >= 2 and not system_prompt:
            system_prompt = self._system_prompt_from_persona(
                str(metadata["name"]), persona
            )
        return CharacterPackage(
            package_id=self._validated_id(metadata["id"]),
            name=str(metadata["name"]),
            version=str(metadata.get("version", "1.0.0")),
            author=str(metadata.get("author", "未知作者")),
            description=str(metadata.get("description", "")),
            system_prompt=system_prompt,
            root=root,
            preview=preview,
            animations=animations,
            schema_version=schema_version,
            quality_tier=(
                str(metadata.get("quality_tier", "legacy"))
                if schema_major >= 2 else "legacy"
            ),
            persona=persona,
            behavior=behavior,
            edge=dict(metadata.get("edge") or {}),
            compatibility=dict(metadata.get("compatibility") or {}),
            rights=dict(metadata.get("rights") or {}),
        )

    @staticmethod
    def _parse_frontmatter(text: str) -> dict:
        if len(text.encode("utf-8")) > MAX_METADATA_BYTES:
            raise CharacterPackageError("character.md 不能超过 1 MB。")
        match = re.match(r"^\s*---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", text, re.S)
        if not match:
            raise CharacterPackageError(
                "character.md 必须以 YAML Front Matter（---）开头。"
            )
        try:
            frontmatter = match.group(1)
            alias_count = sum(
                isinstance(token, yaml.tokens.AliasToken)
                for token in yaml.scan(frontmatter)
            )
            if alias_count > MAX_YAML_ALIASES:
                raise CharacterPackageError(
                    "character.md 的 YAML 别名数量过多。"
                )
            metadata = yaml.safe_load(frontmatter)
        except CharacterPackageError:
            raise
        except (yaml.YAMLError, RecursionError) as exc:
            raise CharacterPackageError(f"character.md 配置无法解析：{exc}") from exc
        if not isinstance(metadata, dict):
            raise CharacterPackageError("character.md 的配置区必须是对象。")
        CharacterPackageManager._validate_yaml_structure(metadata)
        return metadata

    @staticmethod
    def _validate_yaml_structure(value):
        nodes = 0

        def visit(item, depth, active):
            nonlocal nodes
            nodes += 1
            if nodes > MAX_YAML_NODES:
                raise CharacterPackageError(
                    "character.md 的 YAML 结构过于复杂。"
                )
            if depth > MAX_YAML_DEPTH:
                raise CharacterPackageError(
                    "character.md 的 YAML 嵌套层级过深。"
                )
            if not isinstance(item, (dict, list, tuple)):
                return
            identity = id(item)
            if identity in active:
                raise CharacterPackageError(
                    "character.md 不允许循环 YAML 别名。"
                )
            active.add(identity)
            try:
                values = (
                    list(item.items())
                    if isinstance(item, dict)
                    else list(item)
                )
                for child in values:
                    if isinstance(item, dict):
                        key, nested = child
                        visit(key, depth + 1, active)
                        visit(nested, depth + 1, active)
                    else:
                        visit(child, depth + 1, active)
            finally:
                active.remove(identity)

        visit(value, 0, set())

    def _validate_metadata(self, metadata: dict, file_names):
        for key in ("id", "name", "animations"):
            if not metadata.get(key):
                raise CharacterPackageError(f"character.md 缺少必填字段：{key}")
        self._validated_id(str(metadata["id"]))
        schema_major = self._schema_major(metadata)
        if schema_major not in {1, 2}:
            raise CharacterPackageError(
                f"不支持的角色包 schema_version："
                f"{metadata.get('schema_version')}"
            )
        if schema_major >= 2:
            unknown = sorted(set(metadata) - V2_TOP_LEVEL_FIELDS)
            if unknown:
                raise CharacterPackageError(
                    f"不支持的顶层字段：{', '.join(unknown)}"
                )
            missing_fields = sorted(V2_REQUIRED_FIELDS - set(metadata))
            if missing_fields:
                raise CharacterPackageError(
                    f"v2 角色包缺少必填字段：{', '.join(missing_fields)}"
                )
            for key in ("persona", "behavior", "compatibility", "rights"):
                if not isinstance(metadata.get(key), dict):
                    raise CharacterPackageError(f"{key} 必须是对象。")
            initiative = metadata["persona"].get("initiative", {})
            if initiative and not isinstance(initiative, dict):
                raise CharacterPackageError("persona.initiative 必须是对象。")
            if bool(initiative.get("speak_without_prompt", False)):
                raise CharacterPackageError(
                    "v2 角色默认不得主动发言："
                    "persona.initiative.speak_without_prompt 必须为 false。"
                )

        animations = metadata["animations"]
        if not isinstance(animations, dict) or "idle" not in animations:
            raise CharacterPackageError("animations 至少需要定义 idle。")

        declared = []
        total_frames = 0
        preview = metadata.get("preview")
        if preview:
            declared.append(preview)
        normalized_states = set()
        for state, raw in animations.items():
            if state not in SUPPORTED_STATES:
                raise CharacterPackageError(f"不支持的动作状态：{state}")
            normalized_state = STATE_ALIASES.get(state, state)
            if normalized_state in normalized_states:
                raise CharacterPackageError(
                    f"动作别名与标准动作重复定义：{state}"
                )
            normalized_states.add(normalized_state)
            if schema_major >= 2:
                if not isinstance(raw, dict):
                    raise CharacterPackageError(
                        f"v2 动作 {state} 的配置必须是对象。"
                    )
                files = self._v2_animation_files(state, raw)
                playback = str(raw.get("playback", "loop"))
                if playback not in {"once", "loop", "ping_pong"}:
                    raise CharacterPackageError(
                        f"动作 {state} 的 playback 不受支持：{playback}"
                    )
                self._int_pair(raw.get("anchor", [96, 194]), "anchor")
                source = raw["source"]
                frame_count = (
                    len(files)
                    if source.get("type") == "frames"
                    else int(source["frames"])
                )
            else:
                if isinstance(raw, str):
                    files = [raw]
                elif isinstance(raw, dict):
                    files = raw.get("files", [])
                    if isinstance(files, str):
                        files = [files]
                else:
                    raise CharacterPackageError(
                        f"动作 {state} 的配置格式不正确。"
                    )
                frame_count = len(files)
            if not files:
                raise CharacterPackageError(f"动作 {state} 没有图片文件。")
            if frame_count > MAX_FRAMES_PER_ANIMATION:
                raise CharacterPackageError(
                    f"动作 {state} 的帧数不能超过 "
                    f"{MAX_FRAMES_PER_ANIMATION}。"
                )
            total_frames += frame_count
            if total_frames > MAX_TOTAL_ANIMATION_FRAMES:
                raise CharacterPackageError(
                    "角色包动画总帧数过多。"
                )
            declared.extend(files)

        if schema_major >= 2:
            tier = str(metadata.get("quality_tier", ""))
            if tier not in QUALITY_TIER_REQUIRED:
                raise CharacterPackageError(
                    "quality_tier 必须是 basic、standard 或 full。"
                )
            missing_states = sorted(
                QUALITY_TIER_REQUIRED[tier] - normalized_states
            )
            if missing_states:
                tier_name = {
                    "basic": "基础级",
                    "standard": "标准级",
                    "full": "完整级",
                }[tier]
                raise CharacterPackageError(
                    f"{tier_name}角色缺少必需动作："
                    f"{', '.join(missing_states)}"
                )

        for item in declared:
            relative = self._safe_relative(str(item))
            if relative.as_posix() not in file_names:
                raise CharacterPackageError(f"找不到角色资源：{item}")
            if relative.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                raise CharacterPackageError(f"不支持的图片格式：{item}")

    @staticmethod
    def _schema_major(metadata: dict) -> int:
        version = str(metadata.get("schema_version", "1.0"))
        try:
            return int(version.split(".", 1)[0])
        except (TypeError, ValueError) as exc:
            raise CharacterPackageError(
                f"schema_version 格式不正确：{version}"
            ) from exc

    def _v2_animation_files(self, state: str, raw: dict) -> List[str]:
        source = raw.get("source")
        if not isinstance(source, dict):
            raise CharacterPackageError(f"动作 {state} 缺少 source 对象。")
        source_type = source.get("type")
        if source_type == "frames":
            files = source.get("files", [])
            if isinstance(files, str):
                files = [files]
            if not isinstance(files, list) or not all(
                isinstance(item, str) for item in files
            ):
                raise CharacterPackageError(
                    f"动作 {state} 的 source.files 格式不正确。"
                )
            if len(files) > MAX_FRAMES_PER_ANIMATION:
                raise CharacterPackageError(
                    f"动作 {state} 的帧数不能超过 "
                    f"{MAX_FRAMES_PER_ANIMATION}。"
                )
            return files
        if source_type in {"strip", "atlas"}:
            file = source.get("file")
            frames = source.get("frames")
            if not isinstance(file, str) or not file:
                raise CharacterPackageError(
                    f"动作 {state} 的 source.file 不能为空。"
                )
            if (
                isinstance(frames, bool)
                or not isinstance(frames, (int, str))
            ):
                raise CharacterPackageError(
                    f"动作 {state} 的 source.frames 必须是整数。"
                )
            try:
                frame_count = int(frames)
            except ValueError as exc:
                raise CharacterPackageError(
                    f"动作 {state} 的 source.frames 必须是整数。"
                ) from exc
            if not 1 <= frame_count <= 30:
                raise CharacterPackageError(
                    f"动作 {state} 的帧数必须在 1–30 之间。"
                )
            self._int_pair(source.get("cell_size", [192, 208]), "cell_size")
            if source_type == "strip" and source.get(
                "direction", "horizontal"
            ) not in {"horizontal", "vertical"}:
                raise CharacterPackageError(
                    f"动作 {state} 的动作条方向不受支持。"
                )
            if source_type == "atlas":
                for field in ("row", "column"):
                    try:
                        value = int(source.get(field, 0))
                    except (TypeError, ValueError) as exc:
                        raise CharacterPackageError(
                            f"动作 {state} 的 {field} 必须是整数。"
                        ) from exc
                    if value < 0:
                        raise CharacterPackageError(
                            f"动作 {state} 的 {field} 不能小于 0。"
                        )
            return [file]
        raise CharacterPackageError(
            f"动作 {state} 的 source.type 不受支持：{source_type}"
        )

    def _frames_from_animation(
        self, raw: dict, *, root: Path, prefix: str, schema_major: int
    ) -> List[FrameSpec]:
        if schema_major < 2:
            files = raw.get("files", [])
            if isinstance(files, str):
                files = [files]
            return [
                FrameSpec(
                    root / Path(*PurePosixPath(prefix + item).parts)
                )
                for item in files
            ]

        source = raw["source"]
        source_type = source["type"]
        if source_type == "frames":
            files = source.get("files", [])
            if isinstance(files, str):
                files = [files]
            return [
                FrameSpec(
                    root / Path(*PurePosixPath(prefix + item).parts)
                )
                for item in files
            ]

        path = root / Path(*PurePosixPath(prefix + source["file"]).parts)
        count = int(source["frames"])
        cell_w, cell_h = self._int_pair(
            source.get("cell_size", [192, 208]), "cell_size"
        )
        frames = []
        if source_type == "strip":
            horizontal = source.get("direction", "horizontal") == "horizontal"
            for index in range(count):
                rect = (
                    index * cell_w if horizontal else 0,
                    0 if horizontal else index * cell_h,
                    cell_w,
                    cell_h,
                )
                frames.append(FrameSpec(path, rect))
            return frames

        row = int(source.get("row", 0))
        column = int(source.get("column", 0))
        for index in range(count):
            frames.append(FrameSpec(
                path,
                ((column + index) * cell_w, row * cell_h, cell_w, cell_h),
            ))
        return frames

    @staticmethod
    def _int_pair(value, label: str) -> Tuple[int, int]:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
        ):
            raise CharacterPackageError(f"{label} 必须包含两个整数。")
        try:
            pair = int(value[0]), int(value[1])
        except (TypeError, ValueError) as exc:
            raise CharacterPackageError(f"{label} 必须包含两个整数。") from exc
        if pair[0] <= 0 or pair[1] <= 0:
            raise CharacterPackageError(f"{label} 的值必须大于 0。")
        return pair

    @staticmethod
    def _int_value(value: Any, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise CharacterPackageError(f"{label} 必须是整数。")
        try:
            return int(value)
        except ValueError as exc:
            raise CharacterPackageError(f"{label} 必须是整数。") from exc

    @staticmethod
    def _default_priority(state: str) -> int:
        if state == "dragging":
            return 100
        if state in {"alerting_important", "celebrate_live"}:
            return 90
        if state in {"touch", "annoyed"}:
            return 80
        if state in {
            "listening", "thinking", "talking", "working", "success", "failed"
        }:
            return 70
        if state.startswith("edge_") or state in {
            "walk_left", "walk_right", "run_left", "run_right", "jump", "land"
        }:
            return 60
        if state in {"wave", "stretch", "nod", "happy", "alerting"}:
            return 40
        return 10

    @staticmethod
    def _system_prompt_from_persona(name: str, persona: dict) -> str:
        identity = str(persona.get("identity") or f"你是 {name}，Pixkin 桌面伙伴。")
        sections = [identity]
        background = str(persona.get("background") or "").strip()
        if background:
            sections.append(f"背景：{background}")
        traits = persona.get("core_traits") or []
        if isinstance(traits, list) and traits:
            sections.append("核心性格：" + "、".join(map(str, traits)))
        relationship = str(persona.get("relationship") or "").strip()
        if relationship:
            sections.append(f"与用户的关系：{relationship}")
        voice = persona.get("voice") or {}
        if isinstance(voice, dict):
            voice_parts = [
                str(voice.get(key)).strip()
                for key in ("tone", "pacing", "reply_length", "vocabulary")
                if voice.get(key)
            ]
            if voice_parts:
                sections.append("表达方式：" + "；".join(voice_parts))
        tool_behavior = persona.get("tool_behavior") or {}
        if isinstance(tool_behavior, dict) and tool_behavior:
            sections.append(
                "调用工具时先清楚说明目的，完成后准确报告结果；"
                "失败时说明原因，不假装已经成功。"
            )
        boundaries = persona.get("boundaries") or []
        if isinstance(boundaries, list) and boundaries:
            sections.append("行为边界：" + "；".join(map(str, boundaries)))
        sections.append(
            "你可以主动做无声的桌面动作，但不得主动发言、打开窗口、"
            "发送通知或调用工具；只有用户发起交互后才回复。"
        )
        return "\n".join(sections)

    def _validate_directory_images(self, directory: Path, metadata: dict):
        sizes = {}
        for item in self._declared_image_files(metadata):
            path = directory / Path(*self._safe_relative(item).parts)
            sizes[item] = self._read_image_size(path=path)
        self._validate_total_image_pixels(sizes)
        self._validate_v2_geometry(metadata, sizes)

    def _validate_zip_images(self, path: Path, metadata: dict, prefix: str):
        sizes = {}
        with zipfile.ZipFile(path) as opened:
            for item in self._declared_image_files(metadata):
                member = prefix + self._safe_relative(item).as_posix()
                try:
                    raw = opened.read(member)
                except KeyError as exc:
                    raise CharacterPackageError(
                        f"找不到角色资源：{item}"
                    ) from exc
                sizes[item] = self._read_image_size(raw=raw, label=item)
        self._validate_total_image_pixels(sizes)
        self._validate_v2_geometry(metadata, sizes)

    def _declared_image_files(self, metadata: dict) -> List[str]:
        files = []
        if metadata.get("preview"):
            files.append(str(metadata["preview"]))
        schema_major = self._schema_major(metadata)
        for state, raw in metadata.get("animations", {}).items():
            if schema_major >= 2:
                files.extend(self._v2_animation_files(state, raw))
            elif isinstance(raw, str):
                files.append(raw)
            elif isinstance(raw, dict):
                values = raw.get("files", [])
                files.extend([values] if isinstance(values, str) else values)
        return list(dict.fromkeys(files))

    @staticmethod
    def _read_image_size(
        *,
        path: Optional[Path] = None,
        raw: Optional[bytes] = None,
        label: Optional[str] = None,
    ) -> Tuple[int, int]:
        if raw is None and path is None:
            raise CharacterPackageError("缺少待检查的图片来源。")
        if raw is not None:
            source = io.BytesIO(raw)
        else:
            assert path is not None
            source = path
        display = label or str(path)
        try:
            with Image.open(source) as image:
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise CharacterPackageError(
                        f"图片像素总量超过 20 MP：{display}"
                    )
                image.verify()
                return width, height
        except CharacterPackageError:
            raise
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise CharacterPackageError(
                f"角色图片无法解码：{display}"
            ) from exc

    @staticmethod
    def _validate_total_image_pixels(sizes: Dict[str, Tuple[int, int]]):
        total = sum(width * height for width, height in sizes.values())
        if total > MAX_TOTAL_IMAGE_PIXELS:
            raise CharacterPackageError(
                "角色包图片累计像素总量超过 32 MP。"
            )

    def _validate_v2_geometry(self, metadata: dict, sizes: dict):
        if self._schema_major(metadata) < 2:
            return
        for state, raw in metadata["animations"].items():
            source = raw["source"]
            source_type = source["type"]
            if source_type == "frames":
                cell = self._int_pair(
                    source.get("cell_size", [192, 208]), "cell_size"
                )
                files = source.get("files", [])
                if isinstance(files, str):
                    files = [files]
                for item in files:
                    if sizes[item] != cell:
                        raise CharacterPackageError(
                            f"动作 {state} 的独立帧尺寸应为 "
                            f"{cell[0]}×{cell[1]}：{item}"
                        )
                continue
            item = source["file"]
            width, height = sizes[item]
            cell_w, cell_h = self._int_pair(
                source.get("cell_size", [192, 208]), "cell_size"
            )
            count = int(source["frames"])
            if source_type == "strip":
                expected = (
                    (cell_w * count, cell_h)
                    if source.get("direction", "horizontal") == "horizontal"
                    else (cell_w, cell_h * count)
                )
                if (width, height) != expected:
                    raise CharacterPackageError(
                        f"动作 {state} 的动作条尺寸应为 "
                        f"{expected[0]}×{expected[1]}。"
                    )
                continue
            required_w = (int(source.get("column", 0)) + count) * cell_w
            required_h = (int(source.get("row", 0)) + 1) * cell_h
            if width < required_w or height < required_h:
                raise CharacterPackageError(
                    f"动作 {state} 的图集区域超出图片范围。"
                )

    @staticmethod
    def _validated_id(value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,47}", value or ""):
            raise CharacterPackageError(
                "角色包 id 只能使用 2–48 位小写字母、数字、下划线或短横线。"
            )
        return value

    @staticmethod
    def _safe_relative(value: str) -> PurePosixPath:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise CharacterPackageError(f"角色资源路径不安全：{value}")
        for part in path.parts:
            stem = part.split(".", 1)[0].upper()
            if (
                not part
                or part.endswith((" ", "."))
                or stem in WINDOWS_RESERVED_NAMES
                or any(character in WINDOWS_FORBIDDEN_CHARS for character in part)
                or any(ord(character) < 32 for character in part)
            ):
                raise CharacterPackageError(
                    f"角色资源路径不安全：{value}"
                )
        return path

    def _validate_zip_member(self, info: zipfile.ZipInfo):
        self._safe_relative(info.filename.rstrip("/"))
        if info.flag_bits & 0x1:
            raise CharacterPackageError("角色包不能包含加密文件。")
        if info.file_size > MAX_FILE_BYTES:
            raise CharacterPackageError(f"单个文件超过 25 MB：{info.filename}")
        if info.compress_size and info.file_size / info.compress_size > 200:
            raise CharacterPackageError(
                f"角色包文件压缩比异常：{info.filename}"
            )
        unix_mode = info.external_attr >> 16
        if unix_mode and (unix_mode & 0o170000) == 0o120000:
            raise CharacterPackageError("角色包不能包含符号链接。")

    def _relative_member(self, name: str, prefix: str):
        normalized = name.replace("\\", "/")
        if prefix and not normalized.startswith(prefix):
            return None
        relative = normalized[len(prefix):].rstrip("/")
        if not relative:
            return None
        return self._safe_relative(relative)
