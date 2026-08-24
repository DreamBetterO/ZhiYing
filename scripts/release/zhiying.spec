# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


repo = Path(SPECPATH).resolve().parents[1]
icon = repo / "icon" / "知影.ico"

hiddenimports = collect_submodules("zhiying")
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += [
    "PIL._tkinter_finder",
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.ttk",
]

datas = []
for package in ("certifi", "faster_whisper", "reportlab"):
    datas += collect_data_files(package)

binaries = []
for package in ("ctranslate2", "onnxruntime", "av"):
    binaries += collect_dynamic_libs(package)

conda_bin = Path(sys.base_prefix) / "Library" / "bin"
for name in (
    "liblzma.dll",
    "libbz2.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "ffi.dll",
    "libexpat.dll",
    "tcl86t.dll",
    "tk86t.dll",
    "sqlite3.dll",
):
    candidate = conda_bin / name
    if candidate.is_file():
        binaries.append((str(candidate), "."))

a = Analysis(
    [str(Path(SPECPATH) / "entrypoint.py")],
    pathex=[str(repo / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "accelerate",
        "fastapi",
        "torch",
        "torchaudio",
        "torchvision",
        "transformers",
        "uvicorn",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ZhiYing",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon),
)

console = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ZhiYing-Console",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon),
)

coll = COLLECT(gui, console, a.binaries, a.datas, name="ZhiYing")
