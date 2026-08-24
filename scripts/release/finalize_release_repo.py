from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


GITHUB_ASSET_LIMIT = 2 * 1024 * 1024 * 1024
CORE_REQUIRED_PATHS = {
    "ZhiYing.exe",
    "README.md",
    "LICENSE",
    "doctor.cmd",
    "docs/QUICK_START.md",
    "docs/DOWNLOADS.md",
    "docs/GPU_GUIDE.md",
    "docs/TROUBLESHOOTING.md",
    "docs/PRIVACY.md",
    "docs/manifests/components.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _asset(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size >= GITHUB_ASSET_LIMIT:
        raise ValueError(f"release asset exceeds GitHub per-file limit: {path.name}")
    return {"name": path.name, "size": size, "sha256": _sha256(path)}


def _verify_core_archive_documentation(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            executable = next((name for name in names if name.endswith("/ZhiYing.exe")), None)
            if executable is None:
                raise ValueError("core archive documentation gate: ZhiYing.exe is missing")
            prefix = executable[: -len("ZhiYing.exe")]
            missing = sorted(relative for relative in CORE_REQUIRED_PATHS if prefix + relative not in names)
    except zipfile.BadZipFile as exc:
        raise ValueError("core archive documentation gate: invalid ZIP") from exc
    if missing:
        raise ValueError(f"core archive documentation gate: missing {missing}")


def finalize_release_repository(root: Path, *, version: str) -> dict[str, Any]:
    root = root.resolve()
    output = root / "output"
    manifests = root / "manifests"
    checksums = root / "checksums"
    manifests.mkdir(parents=True, exist_ok=True)
    checksums.mkdir(parents=True, exist_ok=True)

    core_path = output / f"ZhiYing-Core-{version}-win-x64.zip"
    _verify_core_archive_documentation(core_path)
    core = _asset(core_path)

    stable = {
        "schema_version": 1,
        "product": "ZhiYing",
        "display_name": "知影",
        "version": version,
        "channel": "stable",
        "platform": "windows-x64",
        "published": False,
        "ready_for_local_review": True,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "release_page": None,
        "asset_delivery": "github-release",
        "assets": {"core": core},
        "external_components": "manifests/components.json",
    }
    (manifests / "stable.json").write_text(
        json.dumps(stable, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (checksums / "SHA256SUMS.txt").write_text(
        f"{core['sha256']}  {core['name']}\n",
        encoding="utf-8",
        newline="\n",
    )
    return stable


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize the local lightweight ZhiYing release directory")
    parser.add_argument("root", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    result = finalize_release_repository(args.root, version=args.version)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
