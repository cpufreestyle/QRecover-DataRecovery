@echo off
chcp 65001 >nul
title QRecover Desktop

cd /d "%~dp0"

REM 检查是否已安装依赖
python -c "import flask" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo 正在安装依赖...
    pip install flask pywebview
)

echo 🚀 QRecover Desktop 启动中...
start "" pythonw qrecover_desktop.py
