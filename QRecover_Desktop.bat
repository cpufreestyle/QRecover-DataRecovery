@echo off
chcp 65001 >nul
title QRecover Desktop - 构建工具

echo ╔══════════════════════════════════════╗
echo ║   QRecover Desktop 构建工具 v2.0    ║
echo ╚══════════════════════════════════════╝
echo.
echo  [1] 运行桌面版 (开发模式)
echo  [2] 打包为独立 EXE (PyInstaller)
echo  [3] 创建桌面快捷方式
echo  [4] 运行 Web 版 (浏览器)
echo  [0] 退出
echo.
set /p choice="请选择 [0-4]: "

if "%choice%"=="1" goto run_desktop
if "%choice%"=="2" goto build_exe
if "%choice%"=="3" goto create_shortcut
if "%choice%"=="4" goto run_web
if "%choice%"=="0" exit /b 0

echo 无效选择！
pause
exit /b 0

:run_desktop
echo.
echo 🚀 启动 QRecover Desktop...
python qrecover_desktop.py
goto end

:build_exe
echo.
echo 📦 正在打包 QRecover Desktop...
echo 这可能需要几分钟，请耐心等待...
echo.
pyinstaller --clean --noconfirm build.spec
if %ERRORLEVEL% NEQ 0 (
    echo ❌ 打包失败！
    pause
    exit /b 1
)
echo.
echo ✅ 打包完成！EXE 位于 dist\QRecoverDesktop.exe
echo.
start explorer dist
goto end

:create_shortcut
echo.
echo 🔗 创建桌面快捷方式...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$WshShell = New-Object -ComObject WScript.Shell; ^
$Shortcut = $WshShell.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\QRecover Desktop.lnk'); ^
$Shortcut.TargetPath = '%CD%\qrecover_desktop.py'; ^
$Shortcut.Arguments = ''; ^
$Shortcut.WorkingDirectory = '%CD%'; ^
$Shortcut.IconLocation = '%CD%\qrecover_icon.ico,0'; ^
$Shortcut.Description = 'QRecover Desktop - 专业数据恢复工具'; ^
$Shortcut.Save()"
echo ✅ 桌面快捷方式已创建！
goto end

:run_web
echo.
echo 🌐 启动 QRecover Web 版...
start http://127.0.0.1:5000
python qrecover.py
goto end

:end
echo.
pause
