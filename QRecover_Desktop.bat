@echo off
chcp 65001 >nul 2>&1
title QRecover Desktop - Build Tool

cd /d "%~dp0"

echo ========================================
echo    QRecover Desktop Build Tool v2.0
echo ========================================
echo.
echo  [1] Run Desktop (Dev Mode)
echo  [2] Build Standalone EXE (PyInstaller)
echo  [3] Create Desktop Shortcut
echo  [4] Run Web Version (Browser)
echo  [0] Exit
echo.
set /p choice="Select [0-4]: "

if "%choice%"=="1" goto run_desktop
if "%choice%"=="2" goto build_exe
if "%choice%"=="3" goto create_shortcut
if "%choice%"=="4" goto run_web
if "%choice%"=="0" exit /b 0

echo Invalid choice!
pause
exit /b 0

:run_desktop
echo.
echo Starting QRecover Desktop...
REM ── Recuva 无感更新清单地址（正式部署改为你的托管 URL）──
set "QRECOVER_RECUVAMANIFESTURL=%~dp0recuva_manifest.json"
python qrecover_desktop.py
goto end

:build_exe
echo.
echo Building QRecover Desktop...
echo This may take a few minutes, please wait...
echo.
pyinstaller --clean --noconfirm build.spec
if %ERRORLEVEL% NEQ 0 (
    echo Build failed!
    pause
    exit /b 1
)
echo.
echo Build complete! EXE at dist\QRecoverDesktop.exe
echo.
start explorer dist
goto end

:create_shortcut
echo.
echo Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\QRecover Desktop.lnk'); $Shortcut.TargetPath = '%CD%\QRecover_Desktop_Quick.bat'; $Shortcut.Arguments = ''; $Shortcut.WorkingDirectory = '%CD%'; $Shortcut.IconLocation = '%CD%\qrecover_icon.ico,0'; $Shortcut.Description = 'QRecover Desktop'; $Shortcut.Save()"
echo Desktop shortcut created!
goto end

:run_web
echo.
echo Starting QRecover Web...
REM ── Recuva 无感更新清单地址（正式部署改为你的托管 URL）──
set "QRECOVER_RECUVAMANIFESTURL=%~dp0recuva_manifest.json"
start http://127.0.0.1:5000
python qrecover.py
goto end

:end
echo.
pause
