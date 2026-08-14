# Windows onedir 发行配置。构建入口：scripts\build_windows.ps1
from pathlib import Path
import shutil

from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path(SPECPATH).resolve()
WHISPER_MODEL = ROOT / "models" / "faster-whisper-small"
node_exe = shutil.which("node")
if not node_exe:
    raise SystemExit("构建需要 Node.js；发行包会随附当前 node.exe。")

datas = [
    (str(ROOT / "scripts" / "render_docx.mjs"), "scripts"),
    (str(ROOT / "scripts" / "qwen_asr_runner.py"), "scripts"),
    (str(ROOT / "node_modules"), "node_modules"),
    (str(ROOT / "icon" / "知影.ico"), "icon"),
    (str(ROOT / "icon" / "知影-产品图标.png"), "icon"),
]
# 只收集模型顶层有效文件，不把 Hugging Face 的下载缓存和 incomplete 重复权重带入发行包。
datas += [
    (str(model_file), "models/faster-whisper-small")
    for model_file in WHISPER_MODEL.iterdir()
    if model_file.is_file()
]
binaries = [(node_exe, "tools/node")]
hiddenimports = collect_submodules("video_study")

# 这些包在运行阶段才导入，显式收集其 Python 模块、数据与动态库。
for package in ("faster_whisper", "ctranslate2", "av", "tokenizers", "huggingface_hub"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    [str(ROOT / "scripts" / "frozen_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide6", "PyQt5", "PyQt6", "fastapi", "uvicorn", "starlette"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="知影",
    icon=str(ROOT / "icon" / "知影.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="知影",
)
