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
    echo ERROR: Python 3.11 was not found at:
    echo %PYTHON_EXE%
    echo Please complete step 2 in the installation guide first.
    if not defined VOICE_ASSISTANT_NO_PAUSE pause
    exit /b 1
)
if not exist "%PYTHONW_EXE%" set "PYTHONW_EXE=%PYTHON_EXE%"

if not exist "%~dp0web_app.py" (
    echo ERROR: web_app.py must be kept in the same folder as this launcher.
    if not defined VOICE_ASSISTANT_NO_PAUSE pause
    exit /b 1
)
if not exist "%~dp01.py" (
    echo ERROR: 1.py must be kept in the same folder as web_app.py.
    if not defined VOICE_ASSISTANT_NO_PAUSE pause
    exit /b 1
)

set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"
set "OLLAMA_HOST=http://127.0.0.1:11434"

if not defined VOICE_ASSISTANT_STARTUP_CHECK_ONLY (
    start "" "%PYTHONW_EXE%" "%~dp0web_app.py"
    exit /b 0
)

"%PYTHON_EXE%" "%~dp01.py"
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" (
    echo.
    echo Voice assistant exited with code %APP_EXIT%.
    if not defined VOICE_ASSISTANT_NO_PAUSE pause
)

exit /b %APP_EXIT%
