@echo off
chcp 65001 >nul 2>&1
title QRecover - Data Recovery Tool
cd /d "%~dp0"

REM Check Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [错误] 启动失败，请确认 Python 已安装
    echo 下载地址: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM ── Recuva 无感更新清单地址 ──
REM 默认指向本地生成的 manifest（仅用于本地验证）。
REM 正式部署：把 recuva_update.zip 与 recuva_manifest.json 传到你的托管地址，
REM 然后把下面这行改成该 manifest 的公开 URL 即可实现自动无感更新。
set "QRECOVER_RECUVAMANIFESTURL=%~dp0recuva_manifest.json"

python qrecover.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [错误] 启动失败，请确认 Python 已安装
    echo.
    pause
)
