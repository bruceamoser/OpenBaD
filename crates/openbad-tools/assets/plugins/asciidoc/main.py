#!/usr/bin/env python3
"""AsciiDoc renderer plugin for OpenBaD."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def emit(payload: dict) -> None:
    print(json.dumps(payload))


def fail(message: str, **metadata: object) -> None:
    emit({"ok": False, "is_error": True, "content": message, "metadata": metadata})
    sys.exit(1)


def resolve(root: Path, value: str, label: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        fail(f"{label} must be workspace-relative: {value}")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        fail(f"{label} escapes the workspace: {value}")
    return resolved


def check() -> None:
    binaries = {name: shutil.which(name) for name in ("asciidoctor", "asciidoctor-pdf")}
    available = binaries["asciidoctor"] is not None
    lines = [
        f"asciidoctor: {binaries['asciidoctor'] or 'missing'}",
        f"asciidoctor-pdf: {binaries['asciidoctor-pdf'] or 'missing'}",
    ]
    if not available:
        lines.append("Install asciidoctor, for example: gem install asciidoctor")
    emit({"ok": available, "content": "\n".join(lines), "metadata": binaries})


def render(arguments: dict, request: dict) -> None:
    root = Path(request.get("workspace") or os.getcwd()).resolve()
    source = resolve(root, str(arguments.get("path") or ""), "path")
    if source.suffix not in {".adoc", ".asciidoc"}:
        fail(f"not an AsciiDoc file: {source.name}")
    if not source.is_file():
        fail(f"AsciiDoc file not found: {source}")
    backend = str(arguments.get("backend") or "html5")
    if backend not in {"html5", "docbook", "pdf"}:
        fail(f"unsupported backend: {backend}")
    tool = "asciidoctor-pdf" if backend == "pdf" else "asciidoctor"
    if shutil.which(tool) is None:
        install = "gem install asciidoctor-pdf" if backend == "pdf" else "gem install asciidoctor"
        fail(f"{tool} is not installed. Install it with: {install}")

    default_suffix = {"html5": ".html", "docbook": ".xml", "pdf": ".pdf"}[backend]
    output_value = arguments.get("output")
    output = (
        resolve(root, str(output_value), "output")
        if output_value
        else source.with_suffix(default_suffix)
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    command = [tool, "--safe-mode=secure", "-b", backend, "-o", str(output)]
    attributes = arguments.get("attributes") or {}
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            command.extend(["-a", f"{key}={value}"])
    command.append(str(source))

    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail(f"{tool} timed out after 90 seconds", command=command)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        fail(f"{tool} failed: {detail}", command=command)

    size = output.stat().st_size if output.exists() else 0
    emit(
        {
            "ok": True,
            "content": f"rendered {source.name} to {output} ({size} bytes)",
            "metadata": {
                "output": str(output.relative_to(root)),
                "bytes": size,
                "backend": backend,
                "command": command,
                "stderr": completed.stderr[-4000:],
            },
        }
    )


def main() -> None:
    try:
        request = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        fail(f"invalid plugin request: {error}")
    action = request.get("action")
    arguments = request.get("arguments") or {}
    if action == "check":
        check()
    elif action == "render":
        render(arguments, request)
    else:
        fail(f"unknown action: {action}")


if __name__ == "__main__":
    main()
