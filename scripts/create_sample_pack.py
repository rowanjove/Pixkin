"""从统一的 v2 资产打包内置角色和可手动导入角色。"""

import zipfile
from pathlib import Path, PurePosixPath

from core.character_package import (
    CharacterPackageManager,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE_ROOT = ROOT / "character-packs" / "v2-built"
BUILTIN_PACKAGES = ("shanshan", "linlin", "pip")
IMPORT_ONLY_PACKAGES = ("yeye",)


def declared_package_files(source: Path):
    manifest = source / "character.md"
    if not manifest.is_file():
        raise FileNotFoundError(f"v2 角色缺少 character.md：{source}")
    metadata = CharacterPackageManager._parse_frontmatter(
        manifest.read_text(encoding="utf-8-sig")
    )
    declared = ["character.md"]
    animations = metadata.get("animations")
    if not isinstance(animations, dict):
        raise ValueError(f"v2 角色 animations 格式无效：{source}")
    for state, animation in animations.items():
        if not isinstance(animation, dict):
            raise ValueError(f"v2 角色动作格式无效：{state}")
        source_config = animation.get("source")
        if not isinstance(source_config, dict):
            raise ValueError(f"v2 角色动作缺少 source：{state}")
        if source_config.get("type") == "frames":
            files = source_config.get("files")
            files = [files] if isinstance(files, str) else files
            if not isinstance(files, list):
                raise ValueError(f"v2 角色动作帧格式无效：{state}")
            declared.extend(str(item) for item in files)
        else:
            declared.append(str(source_config.get("file") or ""))
    preview = metadata.get("preview")
    if preview:
        declared.append(str(preview))
    safe_files = dict.fromkeys(
        CharacterPackageManager._safe_relative(item)
        for item in declared
        if item
    )
    return tuple(
        Path(*PurePosixPath(path).parts)
        for path in safe_files
    )


def build_package(package_id: str) -> Path:
    source = PACKAGE_SOURCE_ROOT / package_id
    output = ROOT / "character-packs" / f"{package_id}.zip"
    package_files = declared_package_files(source)
    temporary = output.with_name(f".{package_id}.tmp.zip")
    try:
        with zipfile.ZipFile(
            temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for relative in package_files:
                path = source / relative
                if not path.is_file():
                    raise FileNotFoundError(f"v2 角色缺少资源：{path}")
                archive.write(path, relative.as_posix())
        validator = CharacterPackageManager.__new__(
            CharacterPackageManager
        )
        validator._read_zip_metadata(temporary)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def main():
    for package_id in (*BUILTIN_PACKAGES, *IMPORT_ONLY_PACKAGES):
        print(build_package(package_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
