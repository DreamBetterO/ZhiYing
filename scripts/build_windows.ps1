param(
    [switch]$SkipTests,
    [switch]$SkipArchive,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pyinstaller = Join-Path $projectRoot ".venv\Scripts\pyinstaller.exe"
$packRoot = Join-Path $projectRoot "pack"
$releaseName = ([string][char]0x77E5) + ([string][char]0x5F71)
$releaseRoot = Join-Path $packRoot $releaseName
$ffmpegBin = Split-Path -Parent (Get-Command ffmpeg.exe -ErrorAction Stop).Source
$ffmpegRoot = Split-Path -Parent $ffmpegBin

if (-not (Test-Path -LiteralPath $pyinstaller)) {
    throw "PyInstaller is not installed in the project virtual environment."
}
if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
    throw "Node.js is required on the build machine."
}

if (-not $SkipTests) {
    & $python -m unittest discover -s (Join-Path $projectRoot "tests")
    if ($LASTEXITCODE -ne 0) { throw "Unit tests failed." }
    & $python -m compileall -q (Join-Path $projectRoot "src") (Join-Path $projectRoot "tests")
    if ($LASTEXITCODE -ne 0) { throw "Python compile check failed." }
}

New-Item -ItemType Directory -Force -Path $packRoot | Out-Null
Push-Location $projectRoot
try {
    & $pyinstaller --noconfirm --clean --distpath $packRoot --workpath (Join-Path $projectRoot "build\pyinstaller") "video-study.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
} finally {
    Pop-Location
}

Copy-Item -Force -LiteralPath (Join-Path $projectRoot "packaging\config.yaml") -Destination (Join-Path $releaseRoot "config.yaml")
Copy-Item -Force -LiteralPath (Join-Path $projectRoot "api.yaml") -Destination (Join-Path $releaseRoot "api.yaml")

$environmentScripts = Get-ChildItem -LiteralPath (Join-Path $projectRoot "packaging") -File |
    Where-Object { $_.Extension -in ".ps1", ".cmd" }
foreach ($script in $environmentScripts) {
    Copy-Item -Force -LiteralPath $script.FullName -Destination (Join-Path $releaseRoot $script.Name)
}
$releaseDocuments = Get-ChildItem -LiteralPath (Join-Path $projectRoot "packaging") -File -Filter "*.md"
foreach ($document in $releaseDocuments) {
    Copy-Item -Force -LiteralPath $document.FullName -Destination (Join-Path $releaseRoot $document.Name)
}
$readme = Get-ChildItem -LiteralPath $packRoot -File -Filter "README-*.md" | Select-Object -First 1
if (-not $readme) { throw "Release README is missing." }
Copy-Item -Force -LiteralPath $readme.FullName -Destination (Join-Path $releaseRoot $readme.Name)

foreach ($directory in ("Resource", "workspace", "output", "tools\ffmpeg")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $releaseRoot $directory) | Out-Null
}
foreach ($tool in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe")) {
    Copy-Item -Force -LiteralPath (Join-Path $ffmpegBin $tool) -Destination (Join-Path $releaseRoot "tools\ffmpeg\$tool")
}
Copy-Item -Force -LiteralPath (Join-Path $ffmpegRoot "LICENSE") -Destination (Join-Path $releaseRoot "tools\ffmpeg\FFmpeg-LICENSE.txt")
if (Test-Path -LiteralPath (Join-Path $ffmpegRoot "README.txt")) {
    Copy-Item -Force -LiteralPath (Join-Path $ffmpegRoot "README.txt") -Destination (Join-Path $releaseRoot "tools\ffmpeg\FFmpeg-README.txt")
}

$zip = Join-Path $packRoot ($releaseName + "-Windows-x64-portable.zip")
if (-not $SkipArchive) {
    if (Test-Path -LiteralPath $zip) {
        Remove-Item -Force -LiteralPath $zip
    }
    Compress-Archive -LiteralPath $releaseRoot -DestinationPath $zip -CompressionLevel Optimal
}

Write-Host "Release directory: $releaseRoot"
if (-not $SkipArchive) {
    Write-Host "Portable archive: $zip"
}

if (-not $SkipInstaller) {
    $isccCandidates = @(
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source,
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $iscc = $isccCandidates | Select-Object -First 1
    if (-not $iscc) {
        throw "Inno Setup 6 is required to build the installer. Use -SkipInstaller for portable-only builds."
    }
    & $iscc (Join-Path $projectRoot "packaging\知影.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }
    Write-Host "Installer: $(Join-Path $packRoot '知影-安装程序-v0.2.0.exe')"
}
