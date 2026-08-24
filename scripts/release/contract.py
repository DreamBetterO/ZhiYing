from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


REQUIRED_COMPONENT_KEYS = {
    "id",
    "display_name",
    "category",
    "version",
    "install_path",
    "source_url",
    "license",
    "size_bytes",
    "required_files",
}


def load_component_catalog(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_component_catalog(catalog: Mapping[str, Any]) -> None:
    if catalog.get("schema_version") != 1:
        raise ValueError("components.json schema_version must be 1")
    components = catalog.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("components.json must contain a non-empty components list")

    seen: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("every component must be an object")
        missing = REQUIRED_COMPONENT_KEYS.difference(component)
        if missing:
            raise ValueError(f"component is missing keys: {sorted(missing)}")
        component_id = str(component["id"])
        if component_id in seen:
            raise ValueError(f"duplicate component id: {component_id}")
        seen.add(component_id)

        url = str(component["source_url"])
        if not url.startswith("https://") or "/latest/" in url.casefold():
            raise ValueError(f"component URL must be pinned HTTPS: {component_id}")

        install_path = PurePosixPath(str(component["install_path"]))
        if install_path.is_absolute() or ".." in install_path.parts:
            raise ValueError(f"unsafe install path: {component_id}")
        if not install_path.parts or install_path.parts[0] not in {"models", "tools"}:
            raise ValueError(f"component must install under models/ or tools/: {component_id}")

        if not isinstance(component["size_bytes"], int) or component["size_bytes"] <= 0:
            raise ValueError(f"invalid component size: {component_id}")
        if not isinstance(component["required_files"], list) or not component["required_files"]:
            raise ValueError(f"component must define required_files: {component_id}")
