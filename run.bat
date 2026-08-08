@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

REM =====================================================
REM TransVideo launcher - double-click to run
REM First run: auto-create .venv and install dependencies
REM IndexTTS 2 env: install on demand from CLI config menu
REM =====================================================

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

REM ----- First run: create .venv -----
if not exist "%VENV_PY%" (
    echo [run] First run, creating virtual environment .venv ...
    where python >nul 2>&1
    if errorlevel 1 (
        echo [run] ERROR: python not found. Install Python 3.10+ and add it to PATH
        pause
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 (
        echo [run] ERROR: failed to create virtual environment
        pause
        exit /b 1
    )
)

REM ----- Dependency check: install when missing -----
"%VENV_PY%" -c "import faster_whisper, edge_tts, openai, yt_dlp" >nul 2>&1
if errorlevel 1 (
    echo [run] Installing dependencies, first run or missing deps ...
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [run] ERROR: dependency installation failed, check your network
        pause
        exit /b 1
    )
)

REM ----- Launch -----
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%VENV_PY%" cli.py %*

pause
