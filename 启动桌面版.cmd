@echo off
cd /d "%~dp0"
call conda activate ImageT10
if errorlevel 1 (
  echo [Video Study] Cannot activate conda environment ImageT10.
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python -c "import langgraph; import langgraph.checkpoint.sqlite" 2>nul
if errorlevel 1 (
  echo [Video Study] ImageT10 is missing V6 runtime dependencies. Install project dependencies and retry.
  pause
  exit /b 1
)
python -m video_study desktop --config config.yaml
