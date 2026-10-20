#!/usr/bin/env python3
"""Validate relative Markdown links, code fences, and Mermaid blocks."""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "target", "dist", ".venv", ".pytest_cache"}
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^```", re.MULTILINE)


def markdown_files() -> list[Path]:
    return [
        path
        for path in sorted(Path(".").rglob("*.md"))
        if not any(part in SKIP_DIRS for part in path.parts)
    ]


def check_links(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    for match in LINK_RE.finditer(text):
        target = match.group(2).strip()
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        resolved = (path.parent / target).resolve()
        if not resolved.exists():
            errors.append(f"{path}: broken link -> {match.group(2)}")
    return errors


def check_fences(path: Path, text: str) -> list[str]:
    if len(FENCE_RE.findall(text)) % 2 != 0:
        return [f"{path}: unbalanced code fences"]
    return []


def check_mermaid(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    for index, block in enumerate(
        re.findall(r"```mermaid\n(.*?)```", text, re.DOTALL), start=1
    ):
        if not block.strip():
            errors.append(f"{path}: empty Mermaid block #{index}")
    return errors


def main() -> int:
    errors: list[str] = []
    files = markdown_files()
    for path in files:
        text = path.read_text(encoding="utf-8")
        errors.extend(check_links(path, text))
        errors.extend(check_fences(path, text))
        errors.extend(check_mermaid(path, text))

    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} documentation error(s) in {len(files)} file(s).")
        return 1

    print(f"OK: {len(files)} Markdown files checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
