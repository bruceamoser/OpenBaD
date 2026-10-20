from __future__ import annotations

import json
from pathlib import Path

import pytest

from openbad.plugins.manifest import (
    CapabilityEntry,
    ManifestError,
    parse_manifest,
)

VALID_MANIFEST: dict = {
    "name": "my_plugin",
    "version": "1.0.0",
    "module": "openbad.plugins.my_plugin",
    "description": "A test plugin",
    "capabilities": [
        {
            "id": "my_plugin.read_file",
            "description": "Reads a file",
            "permissions": ["file.read"],
        },
        {
            "id": "my_plugin.write_db",
            "permissions": ["db.insert", "db.update"],
        },
    ],
}


# ---------------------------------------------------------------------------
# Valid manifest parsing
# ---------------------------------------------------------------------------


def test_parse_valid_dict() -> None:
    manifest = parse_manifest(VALID_MANIFEST)

    assert manifest.name == "my_plugin"
    assert manifest.version == "1.0.0"
    assert manifest.module == "openbad.plugins.my_plugin"
    assert manifest.description == "A test plugin"
    assert len(manifest.capabilities) == 2


def test_parse_valid_json_string() -> None:
    manifest = parse_manifest(json.dumps(VALID_MANIFEST))

    assert manifest.name == "my_plugin"


def test_parse_valid_json_bytes() -> None:
    manifest = parse_manifest(json.dumps(VALID_MANIFEST).encode())

    assert manifest.name == "my_plugin"


def test_parse_valid_json_file(tmp_path: Path) -> None:
    f = tmp_path / "openbad.plugin.json"
    f.write_text(json.dumps(VALID_MANIFEST))

    manifest = parse_manifest(f)

    assert manifest.name == "my_plugin"


def test_capability_fields_populated() -> None:
    manifest = parse_manifest(VALID_MANIFEST)

    cap = manifest.capabilities[0]
    assert cap.id == "my_plugin.read_file"
    assert cap.permissions == ["file.read"]
    assert cap.description == "Reads a file"


def test_optional_description_defaults_to_empty() -> None:
    manifest = parse_manifest(VALID_MANIFEST)

    no_desc_cap = manifest.capabilities[1]
    assert no_desc_cap.description == ""


def test_manifest_is_frozen() -> None:
    manifest = parse_manifest(VALID_MANIFEST)

    with pytest.raises((TypeError, AttributeError)):
        manifest.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Invalid manifests rejected with clear reasons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_field", ["name", "version", "module", "capabilities"])
def test_missing_top_level_field(missing_field: str) -> None:
    data = {k: v for k, v in VALID_MANIFEST.items() if k != missing_field}

    with pytest.raises(ManifestError, match=missing_field):
        parse_manifest(data)


def test_name_empty_string_rejected() -> None:
    data = {**VALID_MANIFEST, "name": "   "}

    with pytest.raises(ManifestError, match="name"):
        parse_manifest(data)


def test_module_empty_string_rejected() -> None:
    data = {**VALID_MANIFEST, "module": ""}

    with pytest.raises(ManifestError, match="module"):
        parse_manifest(data)


def test_capabilities_not_list_rejected() -> None:
    data = {**VALID_MANIFEST, "capabilities": "not-a-list"}

    with pytest.raises(ManifestError, match="capabilities"):
        parse_manifest(data)


def test_capability_missing_id_rejected() -> None:
    data = {
        **VALID_MANIFEST,
        "capabilities": [{"permissions": ["file.read"]}],
    }

    with pytest.raises(ManifestError, match="'id'"):
        parse_manifest(data)


def test_capability_missing_permissions_rejected() -> None:
    data = {
        **VALID_MANIFEST,
        "capabilities": [{"id": "x.y"}],
    }

    with pytest.raises(ManifestError, match="'permissions'"):
        parse_manifest(data)


def test_capability_permissions_not_list_rejected() -> None:
    data = {
        **VALID_MANIFEST,
        "capabilities": [{"id": "x.y", "permissions": "file.read"}],
    }

    with pytest.raises(ManifestError, match="permissions"):
        parse_manifest(data)


def test_invalid_json_string_rejected() -> None:
    with pytest.raises(ManifestError, match="Invalid JSON"):
        parse_manifest("{not valid json")


def test_json_array_rejected() -> None:
    with pytest.raises(ManifestError, match="JSON object"):
        parse_manifest("[]")


def test_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="Cannot read manifest"):
        parse_manifest(tmp_path / "nonexistent.json")


def test_unsupported_source_type_rejected() -> None:
    with pytest.raises(ManifestError, match="Unsupported manifest source"):
        parse_manifest(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_capability_entry_round_trip() -> None:
    cap = CapabilityEntry(id="a.b", permissions=["file.read"], description="desc")
    assert CapabilityEntry.from_dict(cap.to_dict()) == cap
