from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


REQUIRED_FILES = (
    "ZhiYing.exe",
    "ZhiYing-Console.exe",
    "config.yaml",
    "api.yaml",
    "doctor.cmd",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
)
EMPTY_PAYLOAD_DIRECTORIES = ("models", "tools")
DATA_DIRECTORIES = ("Resource", "workspace", "output")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_core_tree(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"core root does not exist: {root}")
    missing = [relative for relative in REQUIRED_FILES if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"missing core files: {', '.join(missing)}")
    if not (root / "_internal").is_dir():
        raise ValueError("missing PyInstaller _internal directory")

    for path in root.rglob("*"):
        if path.is_file() and (path.name == ".env" or path.name.startswith(".env.")):
            raise ValueError(f"secret-like environment file is forbidden: {path.relative_to(root)}")

    payload_files = 0
    for directory in EMPTY_PAYLOAD_DIRECTORIES:
        payload_root = root / directory
        if not payload_root.is_dir():
            raise ValueError(f"missing empty component mount point: {directory}")
        count = sum(1 for path in payload_root.rglob("*") if path.is_file())
        payload_files += count
        if count:
            raise ValueError(f"core package must not contain model/tool payload: {directory}")

    for directory in DATA_DIRECTORIES:
        data_root = root / directory
        if not data_root.is_dir():
            raise ValueError(f"missing data directory: {directory}")
        if any(path.is_file() for path in data_root.rglob("*")):
            raise ValueError(f"core package contains user/generated data: {directory}")

    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.name not in {"BUILD-MANIFEST.json", "SHA256SUMS.txt"}
    ]
    return {
        "product": "ZhiYing",
        "kind": "core",
        "root": str(root),
        "file_count": len(files),
        "size_bytes": sum(path.stat().st_size for path in files),
        "payload_files": payload_files,
    }


def smoke_test_console(root: Path) -> None:
    environment = os.environ.copy()
    system_root = environment.get("SystemRoot", r"C:\Windows")
    environment.update(
        {
            "CLOUD_LLM_ENABLED": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONNOUSERSITE": "1",
            "PATH": os.pathsep.join(
                [str(Path(system_root) / "System32"), system_root]
            ),
        }
    )
    help_result = subprocess.run(
        [str(root / "ZhiYing-Console.exe"), "--help"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if help_result.returncode != 0 or "doctor" not in help_result.stdout:
        raise ValueError(f"console help smoke test failed: {help_result.stderr.strip()}")
    doctor_result = subprocess.run(
        [str(root / "ZhiYing-Console.exe"), "doctor", "--config", str(root / "config.yaml")],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if doctor_result.returncode != 0:
        raise ValueError(f"doctor smoke test failed: {doctor_result.stderr.strip()}")
    report = json.loads(doctor_result.stdout)
    if not all(report["tools"].get(name) is False for name in ("ffmpeg", "ffprobe", "node", "yt-dlp")):
        raise ValueError("core doctor unexpectedly resolved bundled tools")
    if report.get("model_downloaded"):
        raise ValueError("core doctor unexpectedly resolved a bundled model")


def write_manifest(root: Path, *, version: str) -> dict[str, Any]:
    report = verify_core_tree(root)
    rows = []
    for path in sorted((path for path in root.rglob("*") if path.is_file()), key=lambda p: p.as_posix()):
        if path.name in {"BUILD-MANIFEST.json", "SHA256SUMS.txt"}:
            continue
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "schema_version": 1,
        "product": "ZhiYing",
        "version": version,
        "kind": "core",
        "files": rows,
    }
    (root / "BUILD-MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "SHA256SUMS.txt").write_text(
        "".join(f"{row['sha256']}  {row['path']}\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    report["manifest_files"] = len(rows)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a model-free ZhiYing core package")
    parser.add_argument("root", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    report = write_manifest(args.root, version=args.version) if args.write_manifest else verify_core_tree(args.root)
    if args.smoke:
        smoke_test_console(args.root.resolve())
        report["smoke"] = "passed"
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
