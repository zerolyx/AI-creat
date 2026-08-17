@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python 3.11.9 was not found. Run 一键部署并启动.bat first.
    pause
    exit /b 1
)

set PYTHONIOENCODING=utf-8
set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"
"%PYTHON_EXE%" "%~dp0deployment_check.py"
set "CHECK_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %CHECK_EXIT%
