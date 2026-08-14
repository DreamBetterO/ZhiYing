@echo off
cd /d "%~dp0"
call conda activate ImageT10
.venv\Scripts\video-study.exe desktop --config config.yaml
