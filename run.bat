@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

REM TransVideo launcher - double-click to run

set "TRAE_PY=C:\Users\12439\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"

if exist "%TRAE_PY%" (
    echo [run] Python: %TRAE_PY%
    "%TRAE_PY%" cli.py %*
) else (
    echo [run] Using Python from PATH
    python cli.py %*
)

pause
