$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "知影 - 安装 Qwen3-ASR 可选模型"

$appRoot = $PSScriptRoot
$runtimeRoot = Join-Path $appRoot "models\qwen3-asr-runtime"
$modelRoot = Join-Path $appRoot "models\qwen3-asr-0.6b"
$venvPython = Join-Path $runtimeRoot "Scripts\python.exe"

Write-Host ""
Write-Host "知影 · Qwen3-ASR 可选模型安装" -ForegroundColor Cyan
Write-Host "================================"
Write-Host "这会下载独立 Python 运行环境、CUDA 版 PyTorch、qwen-asr 和模型权重。"
Write-Host "预计需要下载约 6–9 GB，并预留至少 12 GB 磁盘空间。"
Write-Host "标准 faster-whisper 模型不受影响，安装失败时仍可继续使用。"
Write-Host ""

$answer = Read-Host "确认开始安装？输入 Y 继续"
if ($answer -notin @("Y", "y")) {
    Write-Host "已取消。"
    exit 0
}

$drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($appRoot).TrimEnd("\").TrimEnd(":"))
if ($drive.Free -lt 12GB) {
    throw "磁盘可用空间不足 12 GB。请释放空间后重试。"
}

function Find-Python312 {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $path = & $launcher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $path) { return $path.Trim() }
    }
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python312\python.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

$python = Find-Python312
if (-not $python) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "未找到 Python 3.12 或 WinGet。请先阅读 Qwen3-ASR-可选模型说明.md。"
    }
    Write-Host "正在通过 WinGet 安装 Python 3.12（当前用户）……" -ForegroundColor Yellow
    winget.exe install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 安装失败。" }
    $python = Find-Python312
}
if (-not $python) { throw "Python 3.12 安装后仍未找到，请重新打开本脚本。" }

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "正在创建隔离运行环境……" -ForegroundColor Yellow
    & $python -m venv $runtimeRoot
    if ($LASTEXITCODE -ne 0) { throw "创建 Qwen 运行环境失败。" }
}

Write-Host "正在安装 CUDA 12.8 版 PyTorch……" -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip wheel
if ($LASTEXITCODE -ne 0) { throw "pip 初始化失败。" }
& $venvPython -m pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw "PyTorch 安装失败。" }

Write-Host "正在安装 Qwen 官方最小 Transformers 运行库……" -ForegroundColor Yellow
& $venvPython -m pip install --upgrade qwen-asr "huggingface_hub[cli]"
if ($LASTEXITCODE -ne 0) { throw "qwen-asr 安装失败。" }

$hf = Join-Path $runtimeRoot "Scripts\hf.exe"
if (-not (Test-Path -LiteralPath $hf)) { throw "未找到 Hugging Face 下载工具。" }
Write-Host "正在下载 Qwen/Qwen3-ASR-0.6B 模型……" -ForegroundColor Yellow
& $hf download Qwen/Qwen3-ASR-0.6B --local-dir $modelRoot
if ($LASTEXITCODE -ne 0) { throw "Qwen3-ASR 模型下载失败。" }

Write-Host ""
Write-Host "Qwen3-ASR 安装完成。" -ForegroundColor Green
Write-Host "请启动知影，在“语音模型链”中填写："
Write-Host "qwen3-asr-0.6b，faster-whisper" -ForegroundColor Cyan
Write-Host ""
Read-Host "按回车键关闭"
