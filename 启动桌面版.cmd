@echo off
setlocal
cd /d "%~dp0"

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m zhiying desktop --config "%~dp0config.yaml"
  goto :finished
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m zhiying desktop --config "%~dp0config.yaml"
  goto :finished
)

where python >nul 2>nul
if not errorlevel 1 (
  python -m zhiying desktop --config "%~dp0config.yaml"
  goto :finished
)

echo [ZhiYing] Python 3.11 or newer was not found.
echo Install Python, then follow the source setup steps in README.md.
pause
exit /b 1

:finished
if errorlevel 1 (
  echo.
  echo [ZhiYing] Startup failed. Install the project dependencies shown in README.md and retry.
  pause
  exit /b 1
)
endlocal
