"""从统一的 v2 资产打包内置角色和可手动导入角色。"""

import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE_ROOT = ROOT / "character-packs" / "v2-built"
BUILTIN_PACKAGES = ("shanshan", "linlin", "pip")
IMPORT_ONLY_PACKAGES = ("yeye",)
PACKAGE_FILES = (
    Path("character.md"),
    Path("spritesheet.webp"),
    Path("images/preview.png"),
)


def build_package(package_id: str) -> Path:
    source = PACKAGE_SOURCE_ROOT / package_id
    output = ROOT / "character-packs" / f"{package_id}.zip"
    if not (source / "character.md").is_file():
        raise FileNotFoundError(f"v2 角色缺少 character.md：{source}")
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(
        output, "w", zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative in PACKAGE_FILES:
            path = source / relative
            if not path.is_file():
                raise FileNotFoundError(f"v2 角色缺少资源：{path}")
            archive.write(path, relative.as_posix())
    return output


def main():
    for package_id in (*BUILTIN_PACKAGES, *IMPORT_ONLY_PACKAGES):
        print(build_package(package_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
