"""Static dependency guardrails for the Pixkin layered architecture.

Usage: ``py -3.11 scripts/check_architecture.py``.  The check is intentionally
small and deterministic so it can run in CI without importing the application.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"__pycache__", ".git", "build", "dist", "venv", ".venv"}


def iter_imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            yield node.module or ""


def check() -> list[str]:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        if any(part in EXCLUDED for part in path.parts):
            continue
        relative = path.relative_to(ROOT)
        layer = relative.parts[0] if relative.parts else ""
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            violations.append(f"{relative}: parse error {type(exc).__name__}")
            continue
        for imported in iter_imports(tree):
            if layer == "ui" and (
                imported == "core.storage"
                or imported.startswith("core.storage.")
                or imported in {"openai", "sqlite3"}
                or imported.startswith("core.providers.chat.openai_compatible")
                or imported.startswith("core.providers.image.")
                or imported.startswith("core.providers.tts.")
            ):
                violations.append(f"{relative}: ui must not import {imported}")
            if layer == "core" and "providers" in relative.parts and imported.startswith("PyQt6"):
                violations.append(f"{relative}: provider must not import {imported}")
            if "plugins" in relative.parts and imported == "ui":
                violations.append(f"{relative}: plugin must not import UI internals")
            if "plugins" in relative.parts and imported.startswith("ui."):
                violations.append(f"{relative}: plugin must not import {imported}")
    return violations


if __name__ == "__main__":
    errors = check()
    if errors:
        print("Architecture violations:")
        print("\n".join(f"- {item}" for item in errors))
        raise SystemExit(1)
    print("Architecture checks passed")
