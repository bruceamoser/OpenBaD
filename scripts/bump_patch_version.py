from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_JSON = ROOT / "wui-svelte" / "package.json"
PACKAGE_LOCK = ROOT / "wui-svelte" / "package-lock.json"
INIT_FILE = ROOT / "src" / "openbad" / "__init__.py"


def _increment(version: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        raise ValueError(f"Unsupported version format: {version}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def _update_pyproject(new_version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    updated = re.sub(
        r'(?m)^version = "\d+\.\d+\.\d+"$',
        f'version = "{new_version}"',
        text,
        count=1,
    )
    PYPROJECT.write_text(updated, encoding="utf-8")


def _update_init(new_version: str) -> None:
    text = INIT_FILE.read_text(encoding="utf-8")
    updated = re.sub(
        r'(?m)^__version__ = "\d+\.\d+\.\d+"$',
        f'__version__ = "{new_version}"',
        text,
        count=1,
    )
    INIT_FILE.write_text(updated, encoding="utf-8")


def _update_package_json(path: Path, new_version: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = new_version
    if path.name == "package-lock.json" and "packages" in data and "" in data["packages"]:
        data["packages"][""]["version"] = new_version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version = "(\d+\.\d+\.\d+)"$', pyproject)
    if match is None:
        raise ValueError("Could not find project version in pyproject.toml")

    old_version = match.group(1)
    new_version = _increment(old_version)

    _update_pyproject(new_version)
    _update_init(new_version)
    _update_package_json(PACKAGE_JSON, new_version)
    _update_package_json(PACKAGE_LOCK, new_version)

    print(f"Bumped version: {old_version} -> {new_version}")


if __name__ == "__main__":
    main()