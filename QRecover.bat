@echo off
chcp 65001 >nul 2>&1
title QRecover - Data Recovery Tool
cd /d "%~dp0"
python qrecover.py
if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请确认 Python 已安装
    echo.
    pause
)
