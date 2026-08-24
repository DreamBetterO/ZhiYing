param(
    [string]$OutputRoot = '',
    [string]$BuildPython = '',
    [string]$TestPython = 'D:\Anaconda\envs\envs\ImageT10\python.exe',
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$releaseScripts = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = [System.IO.Path]::GetFullPath((Join-Path $releaseScripts '..\..'))
$releaseRoot = Join-Path $repo 'release'
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $repo 'release\output'
}
if (-not $BuildPython) {
    $BuildPython = Join-Path $repo '.venv\Scripts\python.exe'
}
$versionText = Get-Content -LiteralPath (Join-Path $repo 'src\zhiying\__init__.py') -Raw
if ($versionText -notmatch '__version__\s*=\s*["'']([^"'']+)["'']') {
    throw 'Unable to resolve ZhiYing version.'
}
$version = $Matches[1]
$output = [System.IO.Path]::GetFullPath($OutputRoot)
$packageName = "ZhiYing-Core-$version-win-x64"
$packageRoot = Join-Path $output $packageName
$archivePath = Join-Path $output "$packageName.zip"
$workRoot = Join-Path $repo 'tmp\release-core-build'

if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
    throw "Build Python not found: $BuildPython"
}
if (-not (Test-Path -LiteralPath $TestPython -PathType Leaf)) {
    throw "Test Python not found: $TestPython"
}
if (Test-Path -LiteralPath $workRoot) {
    Remove-Item -LiteralPath $workRoot -Recurse -Force
}
if (Test-Path -LiteralPath $packageRoot) {
    Remove-Item -LiteralPath $packageRoot -Recurse -Force
}
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}
New-Item -ItemType Directory -Force -Path $workRoot, $output | Out-Null

$env:TEMP = Join-Path $workRoot 'temp'
$env:TMP = $env:TEMP
$env:PYTHONPYCACHEPREFIX = Join-Path $workRoot 'pycache'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $workRoot 'pyinstaller-config'
$env:PYTHONNOUSERSITE = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:CLOUD_LLM_ENABLED = 'false'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

if (-not $SkipTests) {
    & $TestPython -m unittest discover -s (Join-Path $repo 'tests')
    if ($LASTEXITCODE -ne 0) { throw 'Offline acceptance tests failed.' }
    & $TestPython -m compileall -q (Join-Path $repo 'src') (Join-Path $repo 'tests')
    if ($LASTEXITCODE -ne 0) { throw 'Compile check failed.' }
    & $TestPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed.' }
}

$distPath = Join-Path $workRoot 'dist'
$pyinstallerWork = Join-Path $workRoot 'pyinstaller-work'
& $BuildPython -m PyInstaller --noconfirm --clean --distpath $distPath --workpath $pyinstallerWork (Join-Path $releaseScripts 'zhiying.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller core build failed.' }

$sourceCore = Join-Path $distPath 'ZhiYing'
Copy-Item -LiteralPath $sourceCore -Destination $packageRoot -Recurse
Copy-Item -LiteralPath (Join-Path $releaseScripts 'config.core.yaml') -Destination (Join-Path $packageRoot 'config.yaml')
Copy-Item -LiteralPath (Join-Path $repo 'api.yaml') -Destination $packageRoot
Copy-Item -LiteralPath (Join-Path $releaseScripts 'doctor.cmd') -Destination $packageRoot
Copy-Item -LiteralPath (Join-Path $releaseScripts 'CORE-README.md') -Destination (Join-Path $packageRoot 'README.md')
Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination $packageRoot
Copy-Item -LiteralPath (Join-Path $releaseScripts 'THIRD_PARTY_NOTICES.md') -Destination $packageRoot

New-Item -ItemType Directory -Force -Path `
    (Join-Path $packageRoot 'scripts'), `
    (Join-Path $packageRoot 'node_modules'), `
    (Join-Path $packageRoot 'docs'), `
    (Join-Path $packageRoot 'docs\manifests'), `
    (Join-Path $packageRoot 'models'), `
    (Join-Path $packageRoot 'tools'), `
    (Join-Path $packageRoot 'Resource'), `
    (Join-Path $packageRoot 'workspace'), `
    (Join-Path $packageRoot 'output') | Out-Null

Copy-Item -LiteralPath (Join-Path $releaseRoot 'QUICK_START.md') -Destination (Join-Path $packageRoot 'docs\QUICK_START.md')
Copy-Item -LiteralPath (Join-Path $releaseRoot 'DOWNLOADS.md') -Destination (Join-Path $packageRoot 'docs\DOWNLOADS.md')
Copy-Item -LiteralPath (Join-Path $releaseRoot 'GPU_GUIDE.md') -Destination (Join-Path $packageRoot 'docs\GPU_GUIDE.md')
Copy-Item -LiteralPath (Join-Path $releaseRoot 'TROUBLESHOOTING.md') -Destination (Join-Path $packageRoot 'docs\TROUBLESHOOTING.md')
Copy-Item -LiteralPath (Join-Path $releaseRoot 'PRIVACY.md') -Destination (Join-Path $packageRoot 'docs\PRIVACY.md')
Copy-Item -LiteralPath (Join-Path $releaseRoot 'manifests\components.json') -Destination (Join-Path $packageRoot 'docs\manifests\components.json')

Copy-Item -LiteralPath (Join-Path $repo 'scripts\workers\qwen_asr_runner.py') -Destination (Join-Path $packageRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $repo 'scripts\workers\qwen_vl_runner.py') -Destination (Join-Path $packageRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $repo 'scripts\renderers\render_docx.mjs') -Destination (Join-Path $packageRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $repo 'scripts\renderers\render_docx_v31.mjs') -Destination (Join-Path $packageRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $repo 'node_modules\docx') -Destination (Join-Path $packageRoot 'node_modules\docx') -Recurse

Get-ChildItem -LiteralPath $packageRoot -Recurse -Force -File | Unblock-File
& $TestPython (Join-Path $releaseScripts 'verify_core.py') $packageRoot --version $version --write-manifest --smoke
if ($LASTEXITCODE -ne 0) { throw 'Core package verification failed.' }

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $packageRoot,
    $archivePath,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $true
)
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
$summary = [ordered]@{
    product = 'ZhiYing'
    version = $version
    kind = 'core'
    directory = $packageRoot
    archive = $archivePath
    archive_size = (Get-Item -LiteralPath $archivePath).Length
    archive_sha256 = $archiveHash
    cloud_requests = 0
    models_included = $false
    tools_included = $false
    built_at = [DateTimeOffset]::Now.ToString('o')
}
$summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $output 'CORE-BUILD-SUMMARY.json') -Encoding utf8
Write-Host "Core release completed: $archivePath"
