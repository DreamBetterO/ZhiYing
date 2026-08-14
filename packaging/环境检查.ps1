$ErrorActionPreference = "SilentlyContinue"
$Host.UI.RawUI.WindowTitle = "知影 - 环境检查"

function Resolve-Tool([string]$Name) {
    $local = Join-Path $PSScriptRoot "tools\ffmpeg\$Name.exe"
    if (Test-Path -LiteralPath $local) { return $local }
    return (Get-Command "$Name.exe" -ErrorAction SilentlyContinue).Source
}

$ffmpeg = Resolve-Tool "ffmpeg"
$ffprobe = Resolve-Tool "ffprobe"
$ffplay = Resolve-Tool "ffplay"
$gpu = & nvidia-smi.exe --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null
$qwenPython = Join-Path $PSScriptRoot "models\qwen3-asr-runtime\Scripts\python.exe"
$qwenModel = Join-Path $PSScriptRoot "models\qwen3-asr-0.6b\config.json"
$qwenReady = (Test-Path -LiteralPath $qwenPython) -and (Test-Path -LiteralPath $qwenModel)

Write-Host ""
Write-Host "知影运行环境检查" -ForegroundColor Cyan
Write-Host "================"
Write-Host ("FFmpeg : " + $(if ($ffmpeg) { "通过  $ffmpeg" } else { "缺失" })) -ForegroundColor $(if ($ffmpeg) { "Green" } else { "Red" })
Write-Host ("FFprobe: " + $(if ($ffprobe) { "通过  $ffprobe" } else { "缺失" })) -ForegroundColor $(if ($ffprobe) { "Green" } else { "Red" })
Write-Host ("FFplay : " + $(if ($ffplay) { "通过  $ffplay" } else { "缺失（文档回看只能用默认播放器，不能精确定位）" })) -ForegroundColor $(if ($ffplay) { "Green" } else { "Yellow" })
Write-Host ("NVIDIA : " + $(if ($gpu) { "通过  $gpu" } else { "未检测到（仍可自动回退 CPU，速度较慢）" })) -ForegroundColor $(if ($gpu) { "Green" } else { "Yellow" })
Write-Host ("Qwen   : " + $(if ($qwenReady) { "可选模型已安装" } else { "未安装（不影响标准版）" })) -ForegroundColor $(if ($qwenReady) { "Green" } else { "DarkGray" })
Write-Host ""

if (-not $ffmpeg -or -not $ffprobe) {
    Write-Host "发行包中的 FFmpeg 文件可能不完整，请重新安装或解压知影。" -ForegroundColor Yellow
} else {
    Write-Host "基础环境已就绪，可以双击 知影.exe。" -ForegroundColor Green
}
Write-Host ""
Read-Host "按回车键关闭"
