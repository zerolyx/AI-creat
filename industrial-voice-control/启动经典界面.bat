@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0runtime\python\python.exe"
set "PYTHONW_EXE=%~dp0runtime\python\pythonw.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    set "PYTHONW_EXE=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
)
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python 3.11 was not found.
    pause
    exit /b 1
)
if not exist "%PYTHONW_EXE%" set "PYTHONW_EXE=%PYTHON_EXE%"

set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"
set "OLLAMA_HOST=http://127.0.0.1:11434"
start "" "%PYTHONW_EXE%" "%~dp01.py"
