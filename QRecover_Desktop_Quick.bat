@echo off
chcp 65001 >nul 2>&1
title QRecover Desktop

cd /d "%~dp0"

REM Check Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [Error] Python not found. Please install Python first.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Check dependencies
python -c "import flask" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo Installing dependencies...
    pip install flask pywebview
)

echo Starting QRecover Desktop...
start "" pythonw qrecover_desktop.py
