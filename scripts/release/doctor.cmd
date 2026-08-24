@echo off
setlocal
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "CLOUD_LLM_ENABLED=false"
"%~dp0ZhiYing-Console.exe" doctor --config "%~dp0config.yaml"
echo.
echo Press any key to close.
pause >nul
