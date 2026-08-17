@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1"
set "DEPLOY_EXIT=%ERRORLEVEL%"
if not "%DEPLOY_EXIT%"=="0" (
    echo.
    echo Deployment stopped with code %DEPLOY_EXIT%.
    pause
)
exit /b %DEPLOY_EXIT%
