"""Generate the Windows executable version resource from core.version."""

from __future__ import annotations

import argparse
import runpy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = PROJECT_ROOT / "core" / "version.py"
OUTPUT_FILE = PROJECT_ROOT / "version_info.txt"


def application_version() -> str:
    scope = runpy.run_path(str(VERSION_FILE))
    return str(scope["VERSION"])


def numeric_version(version: str) -> tuple[int, int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(
            "VERSION 必须使用三段数字格式，例如 1.3.0。"
        )
    return int(parts[0]), int(parts[1]), int(parts[2]), 0


def render_version_info(version: str) -> str:
    version_tuple = numeric_version(version)
    tuple_text = ", ".join(str(part) for part in version_tuple)
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({tuple_text}),
    prodvers=({tuple_text}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'080404B0',
        [
          StringStruct(u'CompanyName', u'Pixkin'),
          StringStruct(u'FileDescription', u'Pixkin AI 桌面伙伴'),
          StringStruct(u'FileVersion', u'{version}'),
          StringStruct(u'InternalName', u'Pixkin'),
          StringStruct(u'LegalCopyright', u'Copyright © 2026'),
          StringStruct(u'OriginalFilename', u'Pixkin.exe'),
          StringStruct(u'ProductName', u'Pixkin'),
          StringStruct(u'ProductVersion', u'{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="只验证现有 version_info.txt 是否与 VERSION 一致。",
    )
    args = parser.parse_args()

    expected = render_version_info(application_version())
    if args.check:
        actual = (
            OUTPUT_FILE.read_text(encoding="utf-8")
            if OUTPUT_FILE.is_file()
            else ""
        )
        if actual.replace("\r\n", "\n") != expected:
            raise SystemExit(
                "version_info.txt 已过期；请运行 "
                "`py -3.11 scripts/generate_version_info.py`。"
            )
        return 0

    OUTPUT_FILE.write_text(expected, encoding="utf-8", newline="\n")
    print(f"{OUTPUT_FILE.name} ({application_version()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
